import json
import logging
import re
import textwrap
import pydantic

from enum import StrEnum
from result import Result, Ok, Err
from typing import TypeVar, Type
from database import Polls
from telegram import LabeledPrice

# from handlers.start_get_params import StartGetParams
from helpers import constants
from tele_helpers import ModifiedTeleUpdate, TelegramHelpers
from telegram.ext import ContextTypes


class InvoiceTypes(StrEnum):
    INCREASE_VOTER_LIMIT = "INCREASE_VOTER_LIMIT"

    def to_params_template(self):
        match self:
            case InvoiceTypes.INCREASE_VOTER_LIMIT:
                return IncreaseVoterLimitParams

        raise NotImplementedError


P = TypeVar('P', bound=pydantic.BaseModel)


class BasePaymentParams(pydantic.BaseModel):
    invoice_type: InvoiceTypes

    def dump_to_json_str(self) -> str:
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


class IncreaseVoterLimitParams(BasePaymentParams):
    poll_id: int
    voters_increase: int
    invoice_type: InvoiceTypes = InvoiceTypes.INCREASE_VOTER_LIMIT

    def __init__(
        self, poll_id: int, voters_increase: int,
    ):
        super().__init__(
            poll_id=poll_id, voters_increase=voters_increase,
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


class PaymentHandlers(object):
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    async def successful_payment_callback(
        self, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        user = update.user
        user_id = user.get_user_id()
        user_tele_id = user.get_tele_id()

        query = update.pre_checkout_query
        payment = update.message.successful_payment
        telegram_payment_charge_id = payment.telegram_payment_charge_id
        self.logger.warning(textwrap.dedent(f"""
            Payment processed successfully:
            {user_id=} {user_tele_id=}
            {telegram_payment_charge_id=}
            {payment.invoice_payload=}
        """))

        await update.message.reply_text(textwrap.dedent(f"""
            Payment successful 
            Your payment reference ID is {telegram_payment_charge_id}
        """))

        # TODO: save payment into a table for audit purposes first
        load_result = IncreaseVoterLimitParams.safe_load_from_json(
            payment.invoice_payload
        )
        if load_result.is_err():
            return await query.answer(
                ok=False, error_message="Error loading invoice info"
            )

        invoice = load_result.unwrap()
        1 = 2

    async def pre_checkout_callback(
        self, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        query = update.pre_checkout_query
        load_result = IncreaseVoterLimitParams.safe_load_from_json(
            query.invoice_payload
        )
        if load_result.is_err():
            self.logger.error(f"LOAD INVOICE FAILED: {query.invoice_payload}")
            return await query.answer(
                ok=False, error_message="Error loading invoice info"
            )
        else:
            return await query.answer(ok=True)

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
            return await message.reply_text("")

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
        invoice_payload = IncreaseVoterLimitParams(
            poll_id=poll_id, voters_increase=voters_increase
        ).dump_to_json_str()

        # INC_MAX_VOTERS_INVOICE = str(StartGetParams.INC_MAX_VOTERS_INVOICE)
        await context.bot.send_invoice(
            chat_id=message.chat_id,
            title=f"Increase voter limit for Poll #{poll_id}",
            description=(
                f"Increase voter limit from "
                f"{poll.max_voters} to {new_max_voters}"
            ),
            payload=invoice_payload,
            # start_parameter=f"{INC_MAX_VOTERS_INVOICE}={invoice_payload}",
            provider_token="", currency="XTR",
            prices=[LabeledPrice(
                f"Increase to {new_max_voters}", voters_increase
            )],
        )
