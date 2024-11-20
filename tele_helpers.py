import logging
import textwrap
import telegram

from typing import Callable, Coroutine, Any, Dict, Optional, List
from result import Result, Err, Ok

from base_api import BaseAPI, PollInfo
from bot_middleware import track_errors
from helpers.locks_manager import PollsLockManager
from helpers.message_buillder import MessageBuilder

from telegram import Message
from telegram.ext import (
    Application, MessageHandler, CallbackContext, CallbackQueryHandler,
    CommandHandler, ContextTypes
)
# noinspection PyProtectedMember
from telegram.ext._utils.types import CCT, RT
from telegram.ext.filters import BaseFilter
from telegram import (
    Update as BaseTeleUpdate, User as TeleUser
)

from database import Users, Polls, ChatWhitelist
from database.database import UserID

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


class ModifiedTeleUpdate(object):
    def __init__(
        self, update: BaseTeleUpdate, user: Users
    ):
        self.update: BaseTeleUpdate = update
        self.user: Users = user

    @property
    def callback_query(self):
        return self.update.callback_query

    @property
    def message(self):
        return self.update.message

    @property
    def effective_message(self):
        return self.update.effective_message

    @property
    def pre_checkout_query(self):
        return self.update.pre_checkout_query


class TelegramHelpers(object):
    @classmethod
    def _vote_for_poll(
        cls, raw_text: str, user_tele_id: int, username: Optional[str],
        chat_id: Optional[int]
    ) -> Result[tuple[bool, int], MessageBuilder]:
        """
        telegram command format
        /vote {poll_id}: {option_1} > {option_2} > ... > {option_n}
        /vote {poll_id} {option_1} > {option_2} > ... > {option_n}
        example:
        /vote 3: 1 > 2 > 3
        /vote 3 1 > 2 > 3
        :return is_newly_registered, poll_id:
        """
        error_message = MessageBuilder()
        # print('RAW_VOTE_TEXT', [raw_text, user_id])
        if ' ' not in raw_text:
            error_message.add('no poll id specified')
            return Err(error_message)

        unpack_result = BaseAPI.unpack_rankings_and_poll_id(raw_text)

        if unpack_result.is_err():
            assert isinstance(unpack_result, Err)
            return unpack_result

        unpacked_result = unpack_result.unwrap()
        poll_id: int = unpacked_result[0]
        rankings: List[int] = unpacked_result[1]

        # print('PRE_REGISTER')
        register_result = BaseAPI.register_vote(
            poll_id=poll_id, rankings=rankings,
            user_tele_id=user_tele_id, username=username,
            chat_id=chat_id
        )
        if register_result.is_err():
            return register_result

        is_newly_registered = register_result.unwrap()
        return Ok((is_newly_registered, poll_id))

    @classmethod
    async def vote_and_report(
        cls, raw_text: str, user_tele_id: int, message: Message,
        username: Optional[str], chat_id: Optional[int]
    ) -> bool:
        # returns whether vote was successful
        vote_result = cls._vote_for_poll(
            raw_text=raw_text, user_tele_id=user_tele_id,
            username=username, chat_id=chat_id
        )

        if vote_result.is_err():
            error_message = vote_result.err()
            await error_message.call(message.reply_text)
            return False

        _, poll_id = vote_result.unwrap()
        await cls.send_post_vote_reply(message=message, poll_id=poll_id)
        return True

    @classmethod
    async def send_post_vote_reply(cls, message: Message, poll_id: int):
        poll_metadata = Polls.read_poll_metadata(poll_id)
        num_voters = poll_metadata.num_active_voters
        num_votes = poll_metadata.num_votes

        await message.reply_text(textwrap.dedent(f"""
            vote has been registered
            {num_votes} / {num_voters} voted
        """))

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
                await tele_user.send_message("Account has been deleted")
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

    @classmethod
    async def set_chat_registration_status(
        cls, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        whitelist: bool, poll_id: int, add_webapp_link: bool = True
    ) -> bool:
        message = update.message
        tele_user: TeleUser | None = message.from_user

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
            _, is_new_whitelist = ChatWhitelist.build_from_fields(
                poll_id=poll_id, chat_id=message.chat.id
            ).get_or_create()

            if is_new_whitelist:
                reply_msg = 'Whitelisted chat for user self-registration'
                await message.reply_text(reply_msg)
                await cls.view_poll_by_id(
                    update, context, poll_id=poll_id,
                    add_webapp_link=add_webapp_link
                )
            else:
                await message.reply_text('Chat has already been whitelisted')

            return True
        else:
            try:
                whitelist_row = ChatWhitelist.get(
                    (ChatWhitelist.poll == poll_id) &
                    (ChatWhitelist.chat_id == message.chat.id)
                )
            except ChatWhitelist.DoesNotExist:
                await message.reply_text(
                    'Chat was not whitelisted for user self-registration '
                    'to begin with'
                )
                return False

            whitelist_row.delete_instance()
            reply_msg = 'Removed user self-registration chat whitelist'
            await message.reply_text(reply_msg)
            return True

    @classmethod
    async def view_poll_by_id(
        cls, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        poll_id: int, add_webapp_link: bool = True
    ) -> bool:
        user = update.user
        message = update.message
        tele_user: TeleUser | None = update.message.from_user

        user_id = user.get_user_id()
        view_poll_result = BaseAPI.get_poll_message(
            poll_id=poll_id, user_id=user_id,
            bot_username=context.bot.username,
            username=user.username,
            add_webapp_link=add_webapp_link
        )

        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            await error_message.call(message.reply_text)
            return False

        chat_type = update.message.chat.type
        poll_message = view_poll_result.unwrap()
        poll = poll_message.poll_info.metadata

        reply_markup = BaseAPI.generate_vote_markup(
            tele_user=tele_user, poll_id=poll_id, chat_type=chat_type,
            open_registration=poll.open_registration,
            num_options=poll_message.poll_info.max_options
        )

        await message.reply_text(poll_message.text, reply_markup=reply_markup)
        return True

    @classmethod
    async def update_poll_message(
        cls, poll_info: PollInfo, chat_id: int, message_id: int,
        context: CallbackContext, poll_locks_manager: PollsLockManager,
        verbose: bool = False
    ):
        """
        attempts to update the poll info message such that in
        the event that there are multiple simultaneous update attempts
        only the latest update will be propagated
        """
        poll_id = poll_info.metadata.id
        bot_username = context.bot.username
        voter_count = poll_info.metadata.num_active_voters
        poll_locks = await poll_locks_manager.get_poll_locks(
            poll_id=poll_id
        )

        await poll_locks.update_voter_count(voter_count)
        chat_lock = await poll_locks.get_chat_lock(chat_id=chat_id)
        if verbose:
            print('PRE_LOCK', poll_locks_manager.poll_locks_map)

        async with chat_lock:
            if await poll_locks.has_correct_voter_count(voter_count):
                try:
                    poll_display_message = BaseAPI.generate_poll_message(
                        poll_info=poll_info, bot_username=bot_username
                    )
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=poll_display_message.text,
                        reply_markup=poll_display_message.reply_markup
                    )
                finally:
                    await poll_locks_manager.remove_chat_lock(
                        poll_id=poll_id, chat_id=chat_id
                    )
            elif verbose:
                print('IGNORE', voter_count)

        if verbose:
            print('POST_LOCK', poll_locks_manager.poll_locks_map)
