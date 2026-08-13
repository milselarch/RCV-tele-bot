import logging
import textwrap
import telegram
import re

from typing import (
    Callable, Coroutine, Any, Optional, List, Sequence
)

from database import Users
from helpers import constants
from helpers.chat_contexts import PollBuilderTemplate
from result import Result, Err, Ok

from helpers.commands import Command
from helpers.modified_tele_update import ModifiedTeleUpdate, CommandsMapping
from poll_service import PollService, PollInfo
from bot_middleware import track_errors
from helpers.locks_manager import PollsLockManager
from helpers.message_buillder import MessageBuilder

from telegram import Message, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CallbackContext, CallbackQueryHandler,
    CommandHandler, ContextTypes, PreCheckoutQueryHandler
)
# noinspection PyProtectedMember
from telegram.ext._utils.types import CCT, RT
from telegram.ext.filters import BaseFilter
from telegram import (
    Update as BaseTeleUpdate, User as TeleUser
)

from database.database import UserID, PollOptions, Polls, ChatWhitelist
from helpers.rcv_tally import RCVTally, GetPollWinnerInfo
from helpers.redis_cache_manager import GetPollWinnerStatus
from py_rcv import PyEliminationStrategies
from helpers.strings import generate_poll_created_message, NO_MESSAGE_IN_UPDATE

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


class TelegramHelpers(object):
    @classmethod
    def _vote_for_poll(
        cls, raw_text: str, user_tele_id: int, username: Optional[str],
        chat_id: Optional[int]
    ) -> Result[tuple[tuple[bool, bool], int], MessageBuilder]:
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

        unpack_result = PollService.unpack_rankings_and_poll_id(raw_text)

        if unpack_result.is_err():
            assert isinstance(unpack_result, Err)
            return unpack_result

        unpacked_result = unpack_result.unwrap()
        poll_id: int = unpacked_result[0]
        rankings: List[int] = unpacked_result[1]

        # print('PRE_REGISTER')
        register_result = PollService.register_vote(
            poll_id=poll_id, rankings=rankings,
            user_tele_id=user_tele_id, username=username,
            chat_id=chat_id
        )
        if register_result.is_err():
            return Err(register_result.unwrap_err())

        is_newly_registered = register_result.unwrap()
        # TODO: use a dataclass or smth to store all the flags
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
        async def caller(self, update: BaseTeleUpdate, *args, **kwargs):
            # print("SELF", self)
            # print('UPDATE', update, args, kwargs)
            is_tele_update = isinstance(update, BaseTeleUpdate)

            if update.message is not None:
                message: Message = update.message
                tele_user = message.from_user
            elif is_tele_update and update.callback_query is not None:
                query = update.callback_query
                tele_user = query.from_user
            elif update.pre_checkout_query is not None:
                query = update.pre_checkout_query
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

                return await respond_callback("User not found")

            assert tele_user is not None
            tele_id = tele_user.id
            chat_username: str = tele_user.username or ''
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
                update=update, user=user, tele_user=tele_user
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
    def register_pre_checkout_handler(
        cls, dispatcher: Application,
        callback: Callable[[ModifiedTeleUpdate, CCT], Coroutine[Any, Any, RT]]
    ):
        dispatcher.add_handler(PreCheckoutQueryHandler(
            cls.users_middleware(callback, include_self=False)
        ))

    @classmethod
    def register_commands(
        cls, dispatcher: Application,
        commands_mapping: CommandsMapping
    ):
        for command_name in commands_mapping:
            cls.register_command(
                dispatcher=dispatcher,
                command=command_name,
                handler=commands_mapping[command_name]
            )

    @classmethod
    def register_command(
        cls, dispatcher: Application, command: Command,
        handler: Callable[[ModifiedTeleUpdate, ...], Coroutine]
    ):
        wrapped_handler = cls.wrap_command_handler(handler)
        dispatcher.add_handler(CommandHandler(
            command, wrapped_handler
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
        everything after the command but returns an empty string
        if no args are found, or the message is empty
        i.e., /command {args} -> {args}
        """
        message: telegram.Message | None = update.message
        if message is None:
            return ''

        message_text = message.text
        if message_text is None:
            return ''

        raw_text = message_text.strip()
        if ' ' not in raw_text:
            return ''

        # split once on the first contiguous chunk of whitespace
        match = re.search(r'\s+', raw_text)
        if match is None:
            return ''

        raw_args = raw_text[match.end():]
        seperator = raw_text[match.start():match.end()]
        if '\n' in seperator:
            """
            If the character after the command is not a space,
            we will include in the raw_args so that the command handler
            is aware that args were start from a newline
            e.g., /command\n{args} -> \n{args}
            """
            raw_args = '\n' + raw_args

        # slice out everything after the space after the command
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
        whitelist: bool, poll_id: int, add_webapp_link: bool = False
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
                await cls.view_poll_by_id(
                    update, context, poll_id=poll_id,
                    add_webapp_link=add_webapp_link
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
        poll_id: int, add_webapp_link: bool = False
    ) -> bool:
        user = update.user
        message = update.message
        tele_user: TeleUser | None = update.message.from_user

        user_id = user.get_user_id()
        view_poll_result = PollService.get_poll_message(
            poll_id=poll_id, user_id=user_id,
            bot_username=context.bot.username,
            username=user.username,
            add_webapp_link=add_webapp_link,
            add_instructions=update.is_group_chat()
        )

        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            await error_message.call(message.reply_text)
            return False

        chat_type = update.message.chat.type
        poll_message = view_poll_result.unwrap()
        poll = poll_message.poll_info.metadata

        reply_markup = PollService.generate_vote_markup(
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
        verbose: bool = False, add_instructions: bool = True
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
                    poll_display_message = PollService.generate_poll_message(
                        poll_info=poll_info, bot_username=bot_username,
                        add_instructions=add_instructions
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

    @classmethod
    async def handle_poll_winner_request(
        cls, rcv_tally: RCVTally,
        update: ModifiedTeleUpdate, poll_id: int
    ) -> Result[GetPollWinnerInfo, GetPollWinnerStatus]:
        message = update.message
        get_winner_result = await rcv_tally.get_poll_winner(poll_id)

        if get_winner_result.is_err():
            err_status = get_winner_result.unwrap_err()
            await message.reply_text(textwrap.dedent(f"""
                Unexpected error occurred ({err_status})
            """))
            return get_winner_result

        get_winner_info = get_winner_result.unwrap()
        winning_option_id: int | None = get_winner_info.poll_winner_id
        get_status: GetPollWinnerStatus = get_winner_info.status
        poll = get_winner_info.poll
        vote_strategy_name: str = 'unknown'

        try:
            vote_algorithm_no = poll.vote_algorithm
            vote_strategy = PyEliminationStrategies.from_int(vote_algorithm_no)
            vote_strategy_name = vote_strategy.to_stub_string()
        except Exception as e:
            logger.error(f'load vote stat failed: {e}')

        if get_status == GetPollWinnerStatus.COMPUTING:
            await message.reply_text(textwrap.dedent(f"""
                Poll winner computation in progress
                Please check again later
            """))
            return get_winner_result
        elif winning_option_id is not None:
            winning_options = PollOptions.select().where(
                PollOptions.id == winning_option_id
            )

            option_name = winning_options[0].option_name
            await message.reply_text(textwrap.dedent(f"""
                Poll winner is: {option_name}
                (Voting strategy used: {vote_strategy_name})
            """))
            return get_winner_result
        else:
            await message.reply_text(textwrap.dedent(f"""
                Poll has no winner
                (Voting strategy used: {vote_strategy_name})
            """))
            return get_winner_result

    @staticmethod
    async def create_poll(
        update: ModifiedTeleUpdate, user_entry: Users,
        context: ContextTypes.DEFAULT_TYPE,
        raw_poll_creation_args: str, open_registration: bool,
        whitelisted_chat_ids: Sequence[int] = ()
    ) -> bool:
        """
        :param update:
        :param user_entry:
        :param context:
        :param raw_poll_creation_args:
        Everything after the command + space
        e.g., /create_poll {args} -> {args}
        :param open_registration:
        :param whitelisted_chat_ids:
        :return:
        """
        if (message := update.message) is None:
            logger.error(NO_MESSAGE_IN_UPDATE)
            return False
        if (creator_user := message.from_user) is None:
            await message.reply_text("Creator user not specified")
            return False

        creator_tele_id = creator_user.id
        assert isinstance(creator_tele_id, int)
        assert raw_poll_creation_args != ''

        subscription_tier_res = user_entry.get_subscription_tier()
        if subscription_tier_res.is_err():
            err_msg = "Unexpected error reading subscription tier"
            await message.reply_text(err_msg)
            return False

        subscription_tier = subscription_tier_res.unwrap()
        if '\n' not in raw_poll_creation_args:
            await message.reply_text("poll creation format wrong")
            return False

        all_lines = raw_poll_creation_args.split('\n')
        if ':' in raw_poll_creation_args:
            # separate poll voters (before :) from poll title and options
            split_index = raw_poll_creation_args.index(':')
            # first part of command is all the users that are in the poll
            command_p1: str = raw_poll_creation_args[:split_index].strip()
            # second part of command is the poll question + poll options
            command_p2: str = raw_poll_creation_args[split_index+1:].strip()
        else:
            """
            There is no ":" on first line to separate poll voters and
            poll title + questions
            """
            command_p1 = all_lines[0]
            command_p2 = raw_poll_creation_args[len(command_p1)+1:]

        poll_info_lines = command_p2.split('\n')
        if len(poll_info_lines) < 3:
            await message.reply_text('Poll requires at least 2 options')
            return False

        poll_question = poll_info_lines[0].strip().replace('\n', '')
        poll_options = poll_info_lines[1:]
        poll_options = [
            poll_option.strip().replace('\n', '')
            for poll_option in poll_options
        ]
        # print('COMMAND_P2', lines)
        if (command_p1 == '') and not open_registration:
            await message.reply_text('poll voters not specified!')
            return False

        raw_poll_usernames: List[str] = command_p1.split()
        whitelisted_usernames: List[str] = []
        poll_user_tele_ids: List[int] = []

        for raw_poll_user in raw_poll_usernames:
            if raw_poll_user.startswith('#'):
                raw_poll_user_tele_id = raw_poll_user[1:]
                if constants.ID_PATTERN.match(raw_poll_user_tele_id) is None:
                    await message.reply_text(
                        f'Invalid poll user id: {raw_poll_user}'
                    )
                    return False

                poll_user_tele_id = int(raw_poll_user_tele_id)
                poll_user_tele_ids.append(poll_user_tele_id)
                continue

            if raw_poll_user.startswith('@'):
                whitelisted_username = raw_poll_user[1:]
            else:
                whitelisted_username = raw_poll_user

            if len(whitelisted_username) < 4:
                await message.reply_text(
                    f'username too short: {whitelisted_username}'
                )
                return False

            whitelisted_usernames.append(whitelisted_username)

        try:
            db_user = Users.build_from_fields(tele_id=creator_tele_id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        creator_id = db_user.get_user_id()
        # create users if they don't exist
        user_rows = [
            Users.build_from_fields(tele_id=tele_id)
            for tele_id in poll_user_tele_ids
        ]

        poll_builder = PollBuilderTemplate(
            creator_id=creator_id, user_rows=user_rows,
            poll_user_tele_ids=poll_user_tele_ids,
            poll_question=poll_question,
            subscription_tier=subscription_tier,
            open_registration=open_registration,
            poll_options=poll_options,
            whitelisted_usernames=whitelisted_usernames,
            whitelisted_chat_ids=whitelisted_chat_ids
        )

        create_poll_res = poll_builder.save_poll_to_db()
        if create_poll_res.is_err():
            error_message = create_poll_res.unwrap_err()
            await error_message.call(message.reply_text)
            return False

        new_poll: Polls = create_poll_res.unwrap()
        new_poll_id = int(new_poll.id)
        bot_username = context.bot.username

        poll_message = PollService.generate_poll_info(
            new_poll_id, poll_question, poll_options,
            bot_username=bot_username, closed=False,
            num_voters=poll_builder.initial_num_voters,
            max_voters=new_poll.max_voters,
            add_instructions=update.is_group_chat()
        )

        chat_type = message.chat.type
        reply_markup = None

        if chat_type == 'private':
            # create vote button for reply message
            vote_markup_data = PollService.build_private_vote_markup(
                poll_id=new_poll_id, tele_user=creator_user
            )
            reply_markup = ReplyKeyboardMarkup(vote_markup_data)
        elif open_registration:
            vote_markup_data = PollService.build_group_vote_markup(
                poll_id=new_poll_id,
                num_options=len(poll_options)
            )
            reply_markup = InlineKeyboardMarkup(vote_markup_data)

        await message.reply_text(poll_message, reply_markup=reply_markup)
        await message.reply_text(
            generate_poll_created_message(new_poll_id)
        )
        return True
