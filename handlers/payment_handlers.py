import json
import logging
import re
import textwrap
from abc import ABCMeta, abstractmethod

import pydantic

from enum import StrEnum
from result import Result, Ok, Err
from typing import TypeVar, Type
from database import Polls, Payments, db
from telegram import LabeledPrice

from handlers.start_get_params import StartGetParams
# from handlers.start_get_params import StartGetParams
from helpers import constants
from helpers.commands import Command
from tele_helpers import ModifiedTeleUpdate, TelegramHelpers
from telegram.ext import ContextTypes


class InvoiceTypes(StrEnum):
    INCREASE_VOTER_LIMIT = "INCREASE_VOTER_LIMIT"


P = TypeVar('P', bound=pydantic.BaseModel)


class BasePaymentParams(pydantic.BaseModel):
    invoice_type: InvoiceTypes
    payment_id: int = -1

    def dump_to_json_str(self) -> str:
        assert self.payment_id > 0
        return json.dumps(self.model_dump(mode='json'))

    @classmethod
    def load_invoice_type(
        cls, json_str: str
    ) -> Result[InvoiceTypes, ValueError]:
        try:
            model = cls.model_validate_json(json_str)
        except ValueError as e:
            return Err(e)

        return Ok(model.invoice_type)

    @classmethod
    def safe_load_from_json(
        cls: Type[P], json_str: str
    ) -> Result[P, ValueError]:
        try:
            model: P = cls.model_validate_json(json_str)
            return Ok(model)
        except ValueError as e:
            return Err(e)


class IncreaseVoterLimitParams(BasePaymentParams):
    poll_id: int
    voters_increase: int

    def __init__(
        self, poll_id: int, voters_increase: int,
        invoice_type: InvoiceTypes = InvoiceTypes.INCREASE_VOTER_LIMIT,
        **kwargs
    ):
        super().__init__(
            poll_id=poll_id, voters_increase=voters_increase,
            invoice_type=invoice_type, **kwargs
        )

    @classmethod
    def safe_load_from_json(
        cls: Type[P], json_str: str
    ) -> Result[P, ValueError]:
        try:
            model: P = cls.model_validate_json(json_str)
            return Ok(model)
        except ValueError as e:
            return Err(e)


class BasePaymentHandler(object, metaclass=ABCMeta):
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    @abstractmethod
    async def pre_checkout_callback(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        ...

    @abstractmethod
    async def successful_payment_callback(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        payment_charge_id: str
    ):
        ...


class IncreaseVoteLimitHandler(BasePaymentHandler):
    async def pre_checkout_callback(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        query = update.pre_checkout_query
        invoice_payload = query.invoice_payload
        invoice_res = IncreaseVoterLimitParams.safe_load_from_json(
            invoice_payload
        )
        if invoice_res.is_err():
            return await query.answer(
                ok=False, error_message=f"Failed to read invoice data"
            )
        invoice = invoice_res.unwrap()
        poll_id = invoice.poll_id
        poll_res = Polls.build_from_fields(poll_id=poll_id).safe_get()
        if poll_res.is_err():
            return await query.answer(f"Failed to get poll #{poll_id}")

        return await query.answer(ok=True)

    async def successful_payment_callback(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        payment_charge_id: str
    ):
        message = update.message
        payment_info = message.successful_payment
        invoice_payload = payment_info.invoice_payload
        load_invoice_result = IncreaseVoterLimitParams.safe_load_from_json(
            payment_info.invoice_payload
        )
        if load_invoice_result.is_err():
            self.logger.error(f"LOAD INVOICE ERROR FOR: {invoice_payload}")
            return await message.reply_text(
                ok=False, error_message="Error loading invoice info"
            )

        invoice = load_invoice_result.unwrap()
        voters_increase = invoice.voters_increase
        receipt_res = Payments.build_from_fields(
            payment_id=invoice.payment_id
        ).safe_get()

        if receipt_res.is_err():
            self.logger.error(f"RECEIPT GET ERR: {payment_charge_id}")
            return await update.message.reply_text("Error loading receipt")

        invoice = load_invoice_result.unwrap()
        receipt: Payments = receipt_res.unwrap()
        receipt.telegram_payment_charge_id = payment_charge_id
        receipt.paid = True
        receipt.save()

        with db.atomic():
            poll_id = invoice.poll_id
            poll_res = Polls.build_from_fields(poll_id=poll_id).safe_get()
            if poll_res.is_err():
                self.logger.error(f"FAILED TO GET POLL {poll_id}")
                return await message.reply_text(
                    f"Failed to get poll #{poll_id}"
                )

            poll = poll_res.unwrap()
            initial_max_voters = poll.max_voters
            poll.max_voters += voters_increase
            new_max_voters = poll.max_voters
            poll.save()

            receipt.processed = True
            receipt.save()

        reply_message = (
            f"The maximum number of voters for poll #{poll.id} "
            f"has been raised from {initial_max_voters} to {new_max_voters}"
        )
        self.logger.warning(reply_message)
        return await message.reply_text(reply_message)

class PaymentHandlers(object):
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.handlers: dict[InvoiceTypes, Type[BasePaymentHandler]] = {
            InvoiceTypes.INCREASE_VOTER_LIMIT: IncreaseVoteLimitHandler
        }

    async def successful_payment_callback(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        user = update.user
        user_id = user.get_user_id()
        user_tele_id = user.get_tele_id()

        message = update.message
        payment_info = message.successful_payment
        payment_charge_id = payment_info.telegram_payment_charge_id
        invoice_payload = payment_info.invoice_payload

        self.logger.warning(textwrap.dedent(f"""
            Payment processed successfully:
            {user_id=} {user_tele_id=}
            {payment_charge_id=}
            {invoice_payload=}
        """))

        invoice_type_res = BasePaymentParams.load_invoice_type(
            invoice_payload
        )
        if invoice_type_res.is_err():
            self.logger.error(f"LOAD INVOICE FAILED: {invoice_payload}")
            return await message.reply_text(
                ok=False, error_message="Error loading invoice info"
            )

        invoice_type = invoice_type_res.unwrap()
        await message.reply_text(textwrap.dedent(f"""
            Payment successful 
            Your payment reference ID is {payment_charge_id}
        """))

        if invoice_type not in self.handlers:
            error_message = f"Invoice type {invoice_type} unsupported"
            self.logger.error(error_message)
            return await message.reply_text(error_message)

        handler_cls = self.handlers[invoice_type]
        handler = handler_cls(self.logger)
        return await handler.successful_payment_callback(
            update=update, context=context,
            payment_charge_id=payment_charge_id
        )

    async def pre_checkout_callback(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        # TODO: validate receipt has not expired
        query = update.pre_checkout_query
        invoice_payload = query.invoice_payload
        invoice_type_res = BasePaymentParams.load_invoice_type(
            invoice_payload
        )

        if invoice_type_res.is_err():
            self.logger.error(f"LOAD INVOICE FAILED: {invoice_payload}")
            return await query.answer(
                ok=False, error_message="Error loading invoice info"
            )

        invoice_type = invoice_type_res.unwrap()

        if invoice_type not in self.handlers:
            error_message = f"Invoice type {invoice_type} unsupported"
            self.logger.error(error_message)
            return await query.answer(ok=False, error_message=error_message)

        handler_cls = self.handlers[invoice_type]
        handler = handler_cls(self.logger)
        return await handler.pre_checkout_callback(
            update=update, context=context
        )

    async def set_max_voters(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        message = update.message
        raw_args = TelegramHelpers.read_raw_command_args(update)
        user = update.user

        if raw_args == '':
            raise NotImplementedError
        elif constants.ID_PATTERN.match(raw_args) is not None:
            raise NotImplementedError

        # matches two numbers seperated by a space
        pattern = re.compile(r'^([1-9]\d*)\s+([1-9]\d*)$')
        match_result = pattern.match(raw_args)
        if match_result is None:
            return await message.reply_text(textwrap.dedent(f"""
                Invalid arguments
                Command format is:
                /{Command.SET_MAX_VOTERS} {{poll_id}} {{new_voter_limit}}
            """))

        poll_id = int(match_result[1])
        new_max_voters = int(match_result[2])
        # print(f'{poll_id=}, {new_max_voters=}')
        poll_res = Polls.build_from_fields(
            poll_id=poll_id, creator_id=user.get_user_id()
        ).safe_get()

        if poll_res.is_err():
            return await message.reply_text(
                "Only the poll's creator is allowed to change "
                "the max number of voters"
            )
        poll = poll_res.unwrap()
        if poll.max_voters >= new_max_voters:
            return await message.reply_text(
                "New poll max voter limit must be greater "
                "than the existing limit"
            )

        voters_increase = new_max_voters - poll.max_voters
        assert voters_increase > 0
        payment_amount = voters_increase

        invoice = IncreaseVoterLimitParams(
            poll_id=poll_id, voters_increase=voters_increase
        )
        receipt: Payments = Payments.build_from_fields(
            user_id=user.get_user_id(), amount=payment_amount
        ).create()

        receipt_id = receipt.id
        invoice.payment_id = receipt_id
        invoice_payload = invoice.dump_to_json_str()
        receipt.invoice_payload = invoice_payload
        receipt.save()

        # TODO: generate and save receipt ID
        # INC_MAX_VOTERS_INVOICE = str(StartGetParams.INC_MAX_VOTERS_INVOICE)
        await context.bot.send_invoice(
            chat_id=message.chat_id,
            title=f"Increase voter limit for Poll #{poll_id}",
            description=(
                f"Increase voter limit from "
                f"{poll.max_voters} to {new_max_voters} "
                f"(#{receipt_id})"
            ),
            payload=invoice_payload,
            start_parameter=f"{StartGetParams.RECEIPT}={receipt_id}",
            provider_token="", currency="XTR",
            prices=[LabeledPrice(
                f"Increase to {new_max_voters}", payment_amount
            )],
        )
