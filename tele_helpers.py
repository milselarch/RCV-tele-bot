import dataclasses
import logging
import telegram

from typing import Callable, Coroutine, Any, Dict
from result import Result, Err, Ok
from bot_middleware import track_errors
from message_buillder import MessageBuilder
from modified_tele_update import ModifiedTeleUpdate

from telegram import Message
from telegram.ext import (
    Application, MessageHandler, CallbackContext, CallbackQueryHandler,
    CommandHandler
)
# noinspection PyProtectedMember
from telegram.ext._utils.types import CCT, RT
from telegram.ext.filters import BaseFilter
from telegram import (
    Update as BaseTeleUpdate, User as TeleUser
)

from database import Users, Polls, ChatWhitelist
from database.database import UserID, CallbackContextState, ContextStates

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ExtractedContext(object):
    user: Users
    message_text: str
    chat_context: CallbackContextState
    context_type: ContextStates


class TelegramHelpers(object):
    @staticmethod
    def extract_chat_context(
        update: ModifiedTeleUpdate
    ) -> Result[ExtractedContext, MessageBuilder]:
        message: Message = update.message
        user_entry: Users = update.user
        assert isinstance(message.text, str)
        assert len(message.text) > 0
        message_text: str = message.text

        error_message = MessageBuilder()
        chat_context_res = CallbackContextState.build_from_fields(
            user_id=user_entry.get_user_id(), chat_id=message.chat.id
        ).safe_get()

        if chat_context_res.is_err():
            return error_message.add(
                "Use /help to view all available commands, "
                "/create_poll to create a new poll, "
                "or /vote to vote for an existing poll "
            )

        chat_context = chat_context_res.unwrap()
        chat_context_type_res = chat_context.get_context_type()
        if chat_context_type_res.is_err():
            chat_context.delete()
            return error_message.add(
                "Unexpected error loading chat context type"
            )

        chat_context_type = chat_context_type_res.unwrap()
        return Ok(ExtractedContext(
            user=user_entry, message_text=message_text,
            chat_context=chat_context, context_type=chat_context_type
        ))

    @staticmethod
    def users_middleware(
        func: Callable[..., Coroutine], include_self=True
    ) -> Callable[[BaseTeleUpdate, ...], Coroutine]:
        async def caller(
            self, update: BaseTeleUpdate | CallbackContext,
            *args, **kwargs
        ):
            # print("SELF", self)
            # print('UPDATE', update, args, kwargs)
            is_tele_update = isinstance(update, BaseTeleUpdate)

            if update.message is not None:
                message: Message = update.message
                tele_user = message.from_user
            elif is_tele_update and update.callback_query is not None:
                query = update.callback_query
                tele_user = query.from_user
            else:
                tele_user = None

            if tele_user is None:
                if update.message is not None:
                    respond_callback = update.message.reply_text
                elif update.callback_query is not None:
                    respond_callback = update.callback_query.answer
                else:
                    logger.error(f'NO USER FOUND FOR ENDPOINT {func}')
                    return False

                await respond_callback("User not found")

            tele_id = tele_user.id
            chat_username: str = tele_user.username
            assert isinstance(tele_user, TeleUser)
            user, _ = Users.build_from_fields(tele_id=tele_id).get_or_create()
            # don't allow deleted users to interact with the bot
            if user.deleted_at is not None:
                await tele_user.send_message("User has been deleted")
                return False

            # update user tele id to username mapping
            if user.username != chat_username:
                user.username = chat_username
                user.save()

            modified_tele_update = ModifiedTeleUpdate(
                update=update, user=user
            )

            if include_self:
                return await func(self, modified_tele_update, *args, **kwargs)
            else:
                return await func(modified_tele_update, *args, **kwargs)

        def caller_without_self(update: BaseTeleUpdate, *args, **kwargs):
            return caller(None, update, *args, **kwargs)

        return caller if include_self else caller_without_self

    @classmethod
    def register_message_handler(
        cls, dispatcher: Application, message_filter: BaseFilter,
        callback: Callable[[ModifiedTeleUpdate, CCT], Coroutine[Any, Any, RT]]
    ):
        dispatcher.add_handler(MessageHandler(
            message_filter, cls.users_middleware(callback, include_self=False)
        ))

    @classmethod
    def register_callback_handler(
        cls, dispatcher: Application,
        callback: Callable[[ModifiedTeleUpdate, CCT], Coroutine[Any, Any, RT]]
    ):
        dispatcher.add_handler(CallbackQueryHandler(
            cls.users_middleware(callback, include_self=False)
        ))

    @classmethod
    def register_commands(
        cls, dispatcher: Application,
        commands_mapping: Dict[
            str, Callable[[ModifiedTeleUpdate, ...], Coroutine]
        ],
    ):
        for command_name in commands_mapping:
            handler = commands_mapping[command_name]
            wrapped_handler = cls.wrap_command_handler(handler)
            dispatcher.add_handler(CommandHandler(
                command_name, wrapped_handler
            ))

    @classmethod
    def wrap_command_handler(cls, handler):
        return track_errors(cls.users_middleware(
            handler, include_self=False
        ))

    @classmethod
    def read_raw_command_args(
        cls, update: ModifiedTeleUpdate, strip: bool = True
    ) -> str:
        """
        extract the part of the message text that contains
        everything after the command, but returns an empty string
        if no args are found, or the message is empty
        e.g. /command {args} -> {args}
        """
        message: telegram.Message = update.message
        if message is None:
            return ''

        message_text = message.text
        if message_text is None:
            return ''

        raw_text = message_text.strip()
        if ' ' not in raw_text:
            return ''

        raw_args = raw_text[raw_text.index(' ')+1:]
        raw_args = raw_args.strip() if strip else raw_args
        return raw_args

    @classmethod
    def extract_poll_id(
        cls, update: ModifiedTeleUpdate
    ) -> Result[int, MessageBuilder]:
        raw_poll_id = cls.read_raw_command_args(update)
        error_message = MessageBuilder()

        if raw_poll_id == '':
            error_message.add(f'No poll id found')
            return Err(error_message)

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            error_message.add(f'invalid poll id: {raw_poll_id}')
            return Err(error_message)

        return Ok(poll_id)

    @staticmethod
    async def set_chat_registration_status(
        update: ModifiedTeleUpdate, whitelist: bool
    ) -> bool:
        message = update.message
        tele_user: TeleUser | None = message.from_user

        extract_poll_id_result = TelegramHelpers.extract_poll_id(update)
        if extract_poll_id_result.is_err():
            error_message = extract_poll_id_result.err()
            await error_message.call(update.message.reply_text)
            return False

        poll_id = extract_poll_id_result.unwrap()

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            await message.reply_text(f'poll {poll_id} does not exist')
            return False

        try:
            user = Users.build_from_fields(tele_id=tele_user.id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        user_id = user.get_user_id()
        creator_id: UserID = poll.get_creator().get_user_id()
        if creator_id != user_id:
            await message.reply_text(
                'only poll creator is allowed to whitelist chats '
                'for open user registration'
            )
            return False

        if whitelist:
            ChatWhitelist.insert(
                poll_id=poll_id, chat_id=message.chat.id
            ).on_conflict_ignore().execute()
            await message.reply_text(
                f'Whitelisted chat for user self-registration'
            )
            return True
        else:

            try:
                whitelist_row = ChatWhitelist.get(
                    (ChatWhitelist.poll == poll_id) &
                    (ChatWhitelist.chat_id == message.chat.id)
                )
            except ChatWhitelist.DoesNotExist:
                await message.reply_text(
                    f'Chat was not whitelisted for user self-registration '
                    f'to begin with'
                )
                return False

            whitelist_row.delete_instance()
            await message.reply_text(
                f'Removed user self-registration chat whitelist'
            )
            return True

