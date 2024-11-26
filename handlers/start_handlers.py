from abc import ABCMeta, abstractmethod
from typing import Type

from base_api import BaseAPI
from database import Users, Payments, Polls
from handlers.payment_handlers import BasePaymentParams, InvoiceTypes, IncreaseVoterLimitParams, PaymentHandlers
from helpers import strings
from tele_helpers import ModifiedTeleUpdate, TelegramHelpers
from telegram import User as TeleUser, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from helpers.start_get_params import StartGetParams


class BaseMessageHandler(object, metaclass=ABCMeta):
    @abstractmethod
    async def handle_messages(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        raw_payload: str
    ):
        ...


class StartVoteHandler(BaseMessageHandler):
    async def handle_messages(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        raw_payload: str
    ):
        message = update.message
        chat_type = update.message.chat.type

        if chat_type != 'private':
            return await update.message.reply_text(
                'Can only vote with /start in DM'
            )
        try:
            poll_id = int(raw_payload)
        except ValueError:
            return await update.message.reply_text(
                f"Invalid poll id: {raw_payload}"
            )

        tele_user: TeleUser = message.from_user
        assert tele_user is not None
        user: Users = update.user

        user_id = user.get_user_id()
        view_poll_result = BaseAPI.get_poll_message(
            poll_id=poll_id, user_id=user_id,
            bot_username=context.bot.username,
            username=tele_user.username
        )

        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_message = view_poll_result.unwrap()
        reply_markup = ReplyKeyboardMarkup(
            BaseAPI.build_private_vote_markup(
                poll_id=poll_id, tele_user=tele_user
            )
        )
        return await message.reply_text(
            poll_message.text, reply_markup=reply_markup
        )


class WhitelistPollHandler(BaseMessageHandler):
    async def handle_messages(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        raw_payload: str
    ):
        try:
            poll_id = int(raw_payload)
        except ValueError:
            return await update.message.reply_text(
                f"Invalid poll id: {raw_payload}"
            )

        return await TelegramHelpers.set_chat_registration_status(
            update, context, whitelist=True, poll_id=poll_id
        )


class StartPaymentsHandler(BaseMessageHandler):
    async def handle_messages(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        raw_payload: str
    ):
        message = update.message
        user = update.user

        try:
            payment_id = int(raw_payload)
        except ValueError:
            return await message.reply_text(
                f"Invalid payment id: {raw_payload}"
            )

        ref_payment_res = Payments.build_from_fields(
            payment_id=payment_id
        ).safe_get()

        if ref_payment_res.is_err():
            return await message.reply_text("Payment form has expired (3)")

        ref_payment: Payments = ref_payment_res.unwrap()
        invoice_type_res = BasePaymentParams.load_invoice_type(
            ref_payment.invoice_payload
        )
        if invoice_type_res.is_err():
            return await message.reply_text("Failed to load invoice (1)")

        invoice_type = invoice_type_res.unwrap()
        if invoice_type == InvoiceTypes.INCREASE_VOTER_LIMIT:
            safe_load_from_json = IncreaseVoterLimitParams.safe_load_from_json
            load_invoice_res = safe_load_from_json(ref_payment.invoice_payload)
            if load_invoice_res.is_err():
                return await message.reply_text("Failed to load invoice (2)")

            invoice: IncreaseVoterLimitParams = load_invoice_res.unwrap()
            poll_id = invoice.poll_id
            poll_res = Polls.build_from_fields(
                poll_id=poll_id, creator_id=user.get_user_id()
            ).safe_get()

            if poll_res.is_err():
                await message.reply_text(strings.MAX_VOTERS_NOT_EDITABLE)
                return False

            poll = poll_res.unwrap()
            return await PaymentHandlers.set_max_voters_with_params(
                update=update, context=context, poll_id=invoice.poll_id,
                new_max_voters=invoice.voters_increase+poll.max_voters
            )
        else:
            return await message.reply_text(
                f"Invoice type [{invoice_type}] not supported"
            )


class StartHandlers(object):
    def __init__(self):
        self.handlers_mapping: dict[
            StartGetParams, Type[BaseMessageHandler]
        ] = {
            StartGetParams.POLL_ID: StartVoteHandler,
            StartGetParams.WHITELIST_POLL_ID: WhitelistPollHandler,
            StartGetParams.RECEIPT: StartPaymentsHandler
        }

    async def start_handler(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        # Send a message when the command /start is issued.
        message = update.message
        args = context.args

        if len(args) == 0:
            await update.message.reply_text(strings.BOT_STARTED)
            # TODO: check for existing chat contexts
            return True

        command_params: str = args[0]
        assert isinstance(command_params, str)
        invalid_param_msg = f'Invalid params: {args}'
        if '=' not in command_params:
            return await update.message.reply_text(invalid_param_msg)

        seperator_index = command_params.index('=')
        param_name = command_params[:seperator_index]
        param_value = command_params[seperator_index+1:]

        try:
            start_param_enum = StartGetParams(param_name)
        except ValueError:
            return await message.reply_text(invalid_param_msg)

        if start_param_enum not in self.handlers_mapping:
            return await message.reply_text(
                f'Command [{param_name}] not supported'
            )

        context_handler_cls = self.handlers_mapping[start_param_enum]
        context_handler = context_handler_cls()
        return await context_handler.handle_messages(
            update=update, context=context, raw_payload=param_value
        )


start_handlers = StartHandlers()
