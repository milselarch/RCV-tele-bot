from abc import ABCMeta, abstractmethod
from typing import Type

from base_api import BaseAPI
from database import Users
from tele_helpers import ModifiedTeleUpdate, TelegramHelpers
from telegram import User as TeleUser, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from handlers.start_get_params import StartGetParams


class BaseMessageHandler(object, metaclass=ABCMeta):
    @classmethod
    @abstractmethod
    async def handle_messages(
        cls, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        raw_payload: str
    ):
        ...


class StartVoteHandler(BaseMessageHandler):
    @classmethod
    async def handle_messages(
        cls, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
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
    @classmethod
    async def handle_messages(
        cls, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
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


class StartHandlers(object):
    def __init__(self):
        self.handlers_mapping: dict[
            StartGetParams, Type[BaseMessageHandler]
        ] = {
            StartGetParams.POLL_ID: StartVoteHandler,
            StartGetParams.WHITELIST_POLL_ID: WhitelistPollHandler
        }

    async def start_handler(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        # Send a message when the command /start is issued.
        message = update.message
        args = context.args

        if len(args) == 0:
            await update.message.reply_text('Bot started')
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
            return await message.reply_text(f'{param_value} not supported')

        context_handler_cls = self.handlers_mapping[start_param_enum]
        context_handler = context_handler_cls()
        return await context_handler.handle_messages(
            update=update, context=context, raw_payload=param_value
        )


start_handlers = StartHandlers()
