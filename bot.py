from __future__ import annotations

import json
import logging
import multiprocessing
import time

import telegram
import textwrap
import asyncio
import datetime
import re

from peewee import JOIN
from result import Ok, Err, Result
# noinspection PyProtectedMember
from telegram.ext._utils.types import CCT, RT
from telegram.ext.filters import BaseFilter
from MessageBuilder import MessageBuilder
from requests.models import PreparedRequest
from json import JSONDecodeError
from datetime import datetime as Datetime

from ModifiedTeleUpdate import ModifiedTeleUpdate
from SpecialVotes import SpecialVotes
from bot_middleware import track_errors, admin_only
from database.database import UserID
from database.db_helpers import EmptyField, Empty, BoundRowFields
from load_config import WEBHOOK_URL
from LocksManager import PollsLockManager

from typing import (
    List, Tuple, Dict, Optional, Sequence, Iterable, Callable, Coroutine, Any
)
from database import (
    Users, Polls, PollVoters, UsernameWhitelist,
    PollOptions, VoteRankings, db, ChatWhitelist, PollWinners
)
from BaseAPI import (
    BaseAPI, UserRegistrationStatus, PollInfo, SubscriptionTiers,
    CallbackCommands, GetPollWinnerStatus
)
from telegram import (
    Message, WebAppInfo, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
    User as TeleUser, Update as BaseTeleUpdate
)
from telegram.ext import (
    CommandHandler, ContextTypes,
    MessageHandler, filters, CallbackContext, CallbackQueryHandler,
    Application
)

__VERSION__ = '1.1.1'
ID_PATTERN = re.compile(r"^[1-9]\d*$")
MAX_DISPLAY_VOTE_COUNT = 30
MAX_CONCURRENT_UPDATES = 256

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


class RankedChoiceBot(BaseAPI):
    # how long before the delete poll button expires
    DELETE_POLL_BUTTON_EXPIRY = 60
    DELETE_USERS_BACKLOG = datetime.timedelta(days=28)
    FLUSH_USERS_INTERVAL = 600

    def __init__(self, config_path='config.yml'):
        super().__init__()
        self.config_path = config_path
        self.scheduled_processes = []
        self.bot = None
        self.app = None

        self.poll_max_options = 20
        self.poll_option_max_length = 100
        self.poll_locks_manager = PollsLockManager()
        self.webhook_url = None

    @classmethod
    def run_flush_deleted_users(cls):
        asyncio.run(cls.flush_deleted_users())

    @classmethod
    async def flush_deleted_users(cls):
        # TODO: write tests for this
        while True:
            deletion_cutoff = Datetime.now() - cls.DELETE_USERS_BACKLOG
            Users.delete().where(
                Users.deleted_at < deletion_cutoff
            ).execute()

            await asyncio.sleep(cls.FLUSH_USERS_INTERVAL)

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
    def wrap_command_handler(cls, handler):
        return track_errors(cls.users_middleware(
            handler, include_self=False
        ))

    def schedule_tasks(self, tasks: List[Callable[[], None]]):
        assert len(self.scheduled_processes) == 0

        for task in tasks:
            process = multiprocessing.Process(target=task)
            self.scheduled_processes.append(process)
            process.start()

    def start_bot(self):
        assert self.bot is None
        self.bot = self.create_tele_bot()
        self.webhook_url = WEBHOOK_URL
        self.schedule_tasks([
            self.run_flush_deleted_users
        ])

        builder = self.create_application_builder()
        builder.concurrent_updates(MAX_CONCURRENT_UPDATES)
        builder.post_init(self.post_init)
        self.app = builder.build()

        commands_mapping = dict(
            start=self.start_handler,
            user_details=self.user_details_handler,
            chat_details=self.chat_details_handler,
            create_poll=self.create_poll,
            create_group_poll=self.create_group_poll,
            register_user_id=self.register_user_by_tele_id,
            whitelist_chat_registration=self.whitelist_chat_registration,
            blacklist_chat_registration=self.blacklist_chat_registration,

            view_poll=self.view_poll,
            view_polls=self.view_all_polls,
            vote=self.vote_for_poll,
            poll_results=self.fetch_poll_results,
            has_voted=self.has_voted,
            close_poll=self.close_poll,
            view_votes=self.view_votes,
            view_voters=self.view_poll_voters,
            about=self.show_about,
            delete_poll=self.delete_poll,
            delete_account=self.delete_account,
            help=self.show_help,

            vote_admin=self.vote_for_poll_admin,
            close_poll_admin=self.close_poll_admin,
            unclose_poll_admin=self.unclose_poll_admin,
            lookup_from_username_admin=self.lookup_from_username_admin,
            insert_user_admin=self.insert_user_admin
        )

        # on different commands - answer in Telegram
        self.register_commands(
            self.app, commands_mapping=commands_mapping
        )
        # catch-all to handle responses to unknown commands
        self.register_message_handler(
            self.app, filters.Regex(r'^/') & filters.COMMAND,
            self.handle_unknown_command
        )
        # handle web app updates
        self.register_message_handler(
            self.app, filters.StatusUpdate.WEB_APP_DATA,
            self.web_app_handler
        )
        # catch-all to handle all other messages
        self.register_message_handler(
            self.app, filters.Regex(r'.*') & filters.TEXT,
            self.handle_other_messages
        )
        self.register_callback_handler(
            self.app, self.inline_keyboard_handler
        )

        # self.app.add_error_handler(self.error_handler)
        self.app.run_polling(allowed_updates=BaseTeleUpdate.ALL_TYPES)
        print('<<< BOT POLLING LOOP ENDED >>>')
        for process in self.scheduled_processes:
            process.terminate()
            process.join()

    @staticmethod
    async def post_init(application: Application):
        # print('SET COMMANDS')
        await application.bot.set_my_commands([(
            'start', 'start bot'
        ), (
            'user_details', 'shows your username and user id'
        ), (
            'chat_details', 'shows chat id'
        ), (
            'create_poll', 'creates a new poll'
        ), (
            'create_group_poll',
            'creates a new poll that users can self register for'
        ), (
            'register_user_id',
            'registers a user by user_id for a poll'
        ), (
            'whitelist_chat_registration',
            'whitelist a chat for self registration'
        ), (
            'blacklist_chat_registration',
            'removes a chat from self registration whitelist'
        ), (
            'view_poll', 'shows poll details given poll_id'
        ), (
            'view_polls', 'shows all polls that you have created'
        ), (
            'vote', 'vote for the poll with the specified poll_id'
        ), (
            'poll_results',
            'returns poll results if the poll has been closed'
        ), (
            'has_voted',
            "check if you've voted for the poll given the poll ID"
        ), (
            'close_poll',
            'close the poll with the specified poll_id'
        ), (
            'view_votes',
            'view all the votes entered for the poll'
        ), (
            'view_voters',
            'show which voters have voted and which have not'
        ), (
            'about', 'miscellaneous info about the bot'
        ), (
            'delete_poll', 'delete a poll'
        ), (
            'delete_account', 'delete your user account'
        ), (
            'help', 'view commands available to the bot'
        )])

    async def start_handler(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        # Send a message when the command /start is issued.
        message = update.message
        chat_type = update.message.chat.type
        args = context.args
        # print('CONTEXT_ARGS', args)

        if len(args) == 0:
            await update.message.reply_text('Bot started')
            return True
        if chat_type != 'private':
            await update.message.reply_text('Can only vote with /start in DM')
            return False

        pattern_match = re.match('poll_id=([0-9]+)', args[0])

        if not pattern_match:
            await update.message.reply_text(f'Invalid params: {args}')
            return False

        poll_id = int(pattern_match.group(1))
        tele_user: TeleUser = message.from_user
        user: Users = update.user

        user_id = user.get_user_id()
        view_poll_result = self.get_poll_message(
            poll_id=poll_id, user_id=user_id,
            bot_username=context.bot.username,
            username=tele_user.username
        )

        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_message = view_poll_result.unwrap()
        reply_markup = ReplyKeyboardMarkup(self.build_private_vote_markup(
            poll_id=poll_id, tele_user=tele_user
        ))
        await message.reply_text(
            poll_message.text, reply_markup=reply_markup
        )

    @track_errors
    async def web_app_handler(self, update: ModifiedTeleUpdate, _):
        message: Message = update.message
        payload = json.loads(update.effective_message.web_app_data.data)

        try:
            poll_id = int(payload['poll_id'])
            ranked_option_numbers: List[int] = payload['option_numbers']
        except KeyError:
            await message.reply_text('Invalid payload')
            return False

        tele_user: TeleUser = message.from_user
        username: Optional[str] = tele_user.username
        user_tele_id = tele_user.id

        formatted_rankings = ' > '.join([
            self.stringify_ranking(rank) for rank in ranked_option_numbers
        ])
        await message.reply_text(textwrap.dedent(f"""
            Your rankings are:
            {poll_id}: {formatted_rankings}
        """))

        vote_result = self.register_vote(
            poll_id=poll_id, rankings=ranked_option_numbers,
            user_tele_id=user_tele_id, username=username
        )

        if vote_result.is_err():
            error_message = vote_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = vote_result.unwrap()
        await self.send_post_vote_reply(
            message=message, poll_id=poll_id
        )

    @classmethod
    async def send_post_vote_reply(cls, message: Message, poll_id: int):
        poll_metadata = Polls.read_poll_metadata(poll_id)
        num_voters = poll_metadata.num_active_voters
        num_votes = poll_metadata.num_votes

        await message.reply_text(textwrap.dedent(f"""
             vote has been registered
             {num_votes} / {num_voters} voted
         """))

    @track_errors
    async def handle_unknown_command(self, update: ModifiedTeleUpdate, _):
        await update.message.reply_text("Command not found")

    @track_errors
    async def handle_other_messages(self, update: ModifiedTeleUpdate, _):
        # TODO: implement callback contexts.rs voting and poll creation
        await update.message.reply_text(
            "Message support is still in development"
        )

    def generate_poll_url(self, poll_id: int, tele_user: TeleUser) -> str:
        req = PreparedRequest()
        auth_date = str(int(time.time()))
        query_id = self.generate_secret()
        user_info = json.dumps({
            'id': tele_user.id,
            'username': tele_user.username
        })

        data_check_string = self.make_data_check_string(
            auth_date=auth_date, query_id=query_id, user=user_info
        )
        validation_hash = self.sign_data_check_string(
            data_check_string=data_check_string
        )

        params = {
            'poll_id': str(poll_id),
            'auth_date': auth_date,
            'query_id': query_id,
            'user': user_info,
            'hash': validation_hash
        }
        req.prepare_url(self.webhook_url, params)
        return req.url

    def build_private_vote_markup(
        self, poll_id: int, tele_user: TeleUser
    ) -> List[List[KeyboardButton]]:
        poll_url = self.generate_poll_url(
            poll_id=poll_id, tele_user=tele_user
        )
        logger.info(f'POLL_URL = {poll_url}')
        # create vote button for reply message
        markup_layout = [[KeyboardButton(
            text=f'Vote for Poll #{poll_id}', web_app=WebAppInfo(url=poll_url)
        )]]

        return markup_layout

    @track_errors
    async def inline_keyboard_handler(
        self, update: ModifiedTeleUpdate, context: CallbackContext
    ):
        """
        callback method for buttons in chat group messages
        """
        query = update.callback_query
        chat_id = query.message.chat_id
        message_id = query.message.message_id
        raw_callback_data = query.data
        tele_user: TeleUser | None = query.from_user

        if tele_user is None:
            await query.answer("Only users can be registered")
            return False
        if raw_callback_data is None:
            await query.answer("Invalid callback data")
            return False

        try:
            callback_data = json.loads(raw_callback_data)
        except JSONDecodeError:
            await query.answer("Invalid callback data format")
            return False

        if 'command' not in callback_data:
            await query.answer("Callback command unknown")
            return False

        user_tele_id = tele_user.id
        command = callback_data['command']

        try:
            user = Users.build_from_fields(tele_id=user_tele_id).get()
            user_id = user.get_user_id()
        except Users.DoesNotExist:
            await query.answer(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        if command == CallbackCommands.REGISTER:
            poll_id = int(callback_data['poll_id'])

            if not self.is_whitelisted_chat(poll_id=poll_id, chat_id=chat_id):
                await query.answer("Not allowed to register from this chat")
                return False

            registration_status = self._register_voter(
                poll_id=poll_id, user_id=user_id,
                username=tele_user.username
            )

            reply_text = self._reg_status_to_msg(registration_status, poll_id)
            if registration_status != UserRegistrationStatus.REGISTERED:
                await query.answer(reply_text)
                return False

            assert registration_status == UserRegistrationStatus.REGISTERED
            poll_info = self._read_poll_info(poll_id=poll_id)
            notification = query.answer(reply_text)
            poll_message_update = self.update_poll_message(
                poll_info=poll_info, chat_id=chat_id,
                message_id=message_id, context=context
            )
            await asyncio.gather(notification, poll_message_update)

        elif command == CallbackCommands.DELETE:
            poll_id = int(callback_data['poll_id'])
            init_stamp = int(callback_data.get('stamp', 0))
            if time.time() - init_stamp > self.DELETE_POLL_BUTTON_EXPIRY:
                await query.answer("Delete button has expired")
                return False

            poll_query = (Polls.id == poll_id) & (Polls.creator == user_id)
            # TODO: write test to check that only poll creator can delete poll
            poll = Polls.get_or_none(poll_query)

            if poll is None:
                await query.answer(f"Poll #{poll_id} does not exist")
                return False

            assert isinstance(poll, Polls)
            poll_creator = poll.get_creator()
            is_poll_creator = user_id == poll_creator.get_user_id()

            if not is_poll_creator:
                await query.answer(f"Not creator of poll #{poll_id}")
                return False
            elif not poll.closed:
                await query.answer(f"Poll #{poll_id} must be closed first")
                return False

            Polls.delete().where(poll_query).execute()
            await query.answer(f"Poll #{poll_id} deleted")
            # remove delete button after deletion is complete
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=f'Poll #{poll_id} ({poll.desc}) deleted',
                reply_markup=None
            )
            return True

        else:
            await query.answer("Unknown callback command")
            return False

    async def update_poll_message(
        self, poll_info: PollInfo, chat_id: int, message_id: int,
        context: CallbackContext, verbose: bool = False
    ):
        """
        attempts to update the poll info message such that in
        the event that there are multiple simultaneous update attempts
        only the latest update will be propagated
        """
        poll_id = poll_info.metadata.id
        bot_username = context.bot.username
        voter_count = poll_info.metadata.num_active_voters
        poll_locks = await self.poll_locks_manager.get_poll_locks(
            poll_id=poll_id
        )

        await poll_locks.update_voter_count(voter_count)
        chat_lock = await poll_locks.get_chat_lock(chat_id=chat_id)
        if verbose:
            print('PRE_LOCK', self.poll_locks_manager.poll_locks_map)

        async with chat_lock:
            if await poll_locks.has_correct_voter_count(voter_count):
                try:
                    poll_display_message = self._generate_poll_message(
                        poll_info=poll_info, bot_username=bot_username
                    )
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=poll_display_message.text,
                        reply_markup=poll_display_message.reply_markup
                    )
                finally:
                    await self.poll_locks_manager.remove_chat_lock(
                        poll_id=poll_id, chat_id=chat_id
                    )
            elif verbose:
                print('IGNORE', voter_count)

        if verbose:
            print('POST_LOCK', self.poll_locks_manager.poll_locks_map)

    @staticmethod
    def is_whitelisted_chat(poll_id: int, chat_id: int):
        query = ChatWhitelist.select().where(
            (ChatWhitelist.chat_id == chat_id) &
            (ChatWhitelist.poll == poll_id)
        )
        return query.exists()

    @staticmethod
    async def user_details_handler(update: ModifiedTeleUpdate, *_):
        """
        returns current user id and username
        """
        # when command /user_details is invoked
        user: TeleUser = update.message.from_user
        await update.message.reply_text(textwrap.dedent(f"""
            user id: {user.id}
            username: {user.username}
        """))

    @staticmethod
    async def chat_details_handler(update: ModifiedTeleUpdate, *_):
        """
        returns current chat id
        """
        chat_id = update.message.chat.id
        await update.message.reply_text(f"chat id: {chat_id}")

    async def has_voted(self, update: ModifiedTeleUpdate, *_, **__):
        """
        usage:
        /has_voted {poll_id}
        """
        message = update.message
        tele_user: TeleUser | None = update.message.from_user
        user_tele_id = tele_user.id

        extract_poll_id_result = self.extract_poll_id(update)
        if extract_poll_id_result.is_err():
            await message.reply_text('Poll ID not specified')
            return False

        try:
            user = Users.build_from_fields(tele_id=user_tele_id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        user_id = user.get_user_id()
        poll_id = extract_poll_id_result.unwrap()
        is_voter = self.is_poll_voter(
            poll_id=poll_id, user_id=user_id
        )

        if not is_voter:
            await message.reply_text(
                f"You're not a voter of poll {poll_id}"
            )
            return False

        voted = self.check_has_voted(poll_id=poll_id, user_id=user_id)

        if voted:
            await message.reply_text("you've voted already")
        else:
            await message.reply_text("you haven't voted")

    async def create_group_poll(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        """
        /create_group_poll @username_1 @username_2 ... @username_n:
        poll title
        poll option 1
        poll option 2
        ...
        poll option m
        - creates a new poll that chat members can self-register for
        """
        whitelisted_chat_ids = []
        chat_type = update.message.chat.type
        if chat_type != 'private':
            chat_id = update.message.chat.id
            # print('CHAT_ID', chat_id)
            whitelisted_chat_ids.append(chat_id)

        return await self.create_poll(
            update=update, context=context, open_registration=True,
            whitelisted_chat_ids=whitelisted_chat_ids
        )

    async def create_poll(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        open_registration: bool = False,
        whitelisted_chat_ids: Sequence[int] = ()
    ):
        """
        example:
        ---------------------------
        /create_poll @asd @fad:
        what ice cream is the best
        mochi
        potato
        cookies and cream
        chocolate
        """
        message: Message = update.message
        creator_user: TeleUser | None = message.from_user
        if creator_user is None:
            await message.reply_text("Creator user not specified")
            return False

        creator_tele_id = creator_user.id
        assert isinstance(creator_tele_id, int)
        raw_text = message.text.strip()
        user_entry: Users = update.user

        try:
            subscription_tier = SubscriptionTiers(
                user_entry.subscription_tier
            )
        except ValueError:
            await message.reply_text("Creator user does not exist")
            return False

        if '\n' not in raw_text:
            await message.reply_text("poll creation format wrong")
            return False
        elif ':' not in raw_text:
            await message.reply_text("poll creation format wrong")
            return False

        all_lines = raw_text.split('\n')
        if ':' in all_lines[0]:
            # separate poll voters (before :) from poll title and options
            split_index = raw_text.index(':')
            # first part of command is all the users that are in the poll
            command_p1: str = raw_text[:split_index].strip()
            # second part of command is the poll question + poll options
            command_p2: str = raw_text[split_index + 1:].strip()
        else:
            # no : on first line to separate poll voters and
            # poll title + questions
            command_p1 = ''
            command_p2 = raw_text

        lines = command_p2.split('\n')
        if len(lines) < 3:
            await message.reply_text('Poll requires at least 2 options')
            return False

        poll_question = lines[0].strip().replace('\n', '')
        poll_options = lines[1:]
        poll_options = [
            poll_option.strip().replace('\n', '')
            for poll_option in poll_options
        ]

        if len(poll_options) > self.poll_max_options:
            await message.reply_text(textwrap.dedent(f"""
                Poll can have at most {self.poll_max_options} options
                {len(poll_options)} poll options passed
            """))
            return False

        max_option_length = max([len(option) for option in poll_options])
        if max_option_length > self.poll_option_max_length:
            await message.reply_text(textwrap.dedent(f"""
                Poll option character limit is {self.poll_option_max_length}
                Longest option passed is {max_option_length} characters long
            """))
            return False

        # print('COMMAND_P2', lines)
        if ' ' in command_p1:
            command_p1 = command_p1[command_p1.index(' '):].strip()
        elif not open_registration:
            await message.reply_text('poll voters not specified!')
            return False
        else:
            command_p1 = ''

        raw_poll_usernames: List[str] = command_p1.split()
        whitelisted_usernames: List[str] = []
        poll_user_tele_ids: List[int] = []

        for raw_poll_user in raw_poll_usernames:
            if raw_poll_user.startswith('#'):
                raw_poll_user_tele_id = raw_poll_user[1:]
                if ID_PATTERN.match(raw_poll_user_tele_id) is None:
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

        duplicate_tele_ids = self.get_duplicate_nums(poll_user_tele_ids)
        if len(duplicate_tele_ids) > 0:
            await message.reply_text(
                f'Duplicate user ids found: {duplicate_tele_ids}'
            )
            return False

        assert len(set(duplicate_tele_ids)) == len(duplicate_tele_ids)
        initial_num_voters = (
            len(poll_user_tele_ids) + len(whitelisted_usernames)
        )
        max_voters = subscription_tier.get_max_voters()
        if initial_num_voters > max_voters:
            await message.reply_text(f'Whitelisted voters exceeds limit')
            return False

        try:
            user = Users.build_from_fields(tele_id=creator_tele_id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        creator_id = user.get_user_id()
        assert initial_num_voters <= max_voters
        num_user_created_polls = self.count_polls_created(creator_id)
        poll_creation_limit = subscription_tier.get_max_polls()
        limit_reached_text = textwrap.dedent(f"""
            Poll creation limit reached
            Use /delete {{POLL_ID}} to remove unused polls
        """)

        if num_user_created_polls >= poll_creation_limit:
            await message.reply_text(limit_reached_text)
            return False

        # create users if they don't exist
        user_rows = [
            Users.build_from_fields(tele_id=tele_id)
            for tele_id in poll_user_tele_ids
        ]

        with db.atomic():
            Users.batch_insert(user_rows).on_conflict_ignore().execute()
            query = Users.tele_id.in_(poll_user_tele_ids)
            users = Users.select().where(query)
            poll_user_ids = [user.get_user_id() for user in users]

            assert len(poll_user_ids) == len(poll_user_tele_ids)
            num_user_created_polls = self.count_polls_created(creator_id)
            # verify again that the number of polls created is still
            # within the limit to prevent race conditions
            if num_user_created_polls >= poll_creation_limit:
                await message.reply_text(limit_reached_text)
                return False

            new_poll = Polls.build_from_fields(
                desc=poll_question, creator_id=creator_id,
                num_voters=initial_num_voters, open_registration=open_registration,
                max_voters=subscription_tier.get_max_voters()
            ).create()

            new_poll_id: int = new_poll.id
            assert isinstance(new_poll_id, int)
            chat_whitelist_rows: List[BoundRowFields[ChatWhitelist]] = []
            whitelist_user_rows: List[BoundRowFields[UsernameWhitelist]] = []
            poll_option_rows: List[BoundRowFields[PollOptions]] = []
            poll_voter_rows: List[BoundRowFields[PollVoters]] = []

            # create poll options
            for k, poll_option in enumerate(poll_options):
                poll_choice_number = k+1
                poll_option_rows.append(PollOptions.build_from_fields(
                    poll_id=new_poll_id, option_name=poll_option,
                    option_number=poll_choice_number
                ))
            # whitelist voters in poll by username
            for raw_poll_user in whitelisted_usernames:
                row_fields = UsernameWhitelist.build_from_fields(
                    poll_id=new_poll_id, username=raw_poll_user
                )
                whitelist_user_rows.append(row_fields)
            # whitelist voters in poll by user id
            for poll_user_id in poll_user_ids:
                poll_voter_rows.append(PollVoters.build_from_fields(
                    poll_id=new_poll_id, user_id=poll_user_id
                ))
            # chat ids that are whitelisted for user self-registration
            for chat_id in whitelisted_chat_ids:
                chat_whitelist_rows.append(ChatWhitelist.build_from_fields(
                    poll_id=new_poll_id, chat_id=chat_id
                ))

            PollVoters.batch_insert(poll_voter_rows).execute()
            UsernameWhitelist.batch_insert(whitelist_user_rows).execute()
            PollOptions.batch_insert(poll_option_rows).execute()
            ChatWhitelist.batch_insert(chat_whitelist_rows).execute()

        bot_username = context.bot.username
        poll_message = self.generate_poll_info(
            new_poll_id, poll_question, poll_options,
            bot_username=bot_username, closed=False,
            num_voters=initial_num_voters
        )

        chat_type = update.message.chat.type
        reply_markup = None

        if chat_type == 'private':
            # create vote button for reply message
            vote_markup_data = self.build_private_vote_markup(
                poll_id=new_poll_id, tele_user=creator_user
            )
            reply_markup = ReplyKeyboardMarkup(vote_markup_data)
        elif open_registration:
            vote_markup_data = self.build_group_vote_markup(
                poll_id=new_poll_id
            )
            reply_markup = InlineKeyboardMarkup(vote_markup_data)

        await message.reply_text(
            poll_message, reply_markup=reply_markup
        )

    async def register_user_by_tele_id(
        self, update: ModifiedTeleUpdate, *_, **__
    ):
        """
        registers a user by user_tele_id for a poll
        /whitelist_user_id {poll_id} {user_tele_id}
        """
        message: Message = update.message
        tele_user = message.from_user
        raw_text = message.text.strip()
        pattern = re.compile(r'^\S+\s+([1-9]\d*)\s+([1-9]\d*)$')
        matches = pattern.match(raw_text)

        if tele_user is None:
            await message.reply_text(f'user not found')
            return False
        if matches is None:
            await message.reply_text(f'Format invalid')
            return False

        capture_groups = matches.groups()
        if len(capture_groups) != 2:
            await message.reply_text(f'Format invalid')
            return False

        poll_id = int(capture_groups[0])
        target_user_tele_id = int(capture_groups[1])

        try:
            target_user = Users.build_from_fields(
                tele_id=target_user_tele_id
            ).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            await message.reply_text(f'poll {poll_id} does not exist')
            return False

        user_id = target_user.get_user_id()
        creator_id: UserID = poll.get_creator().get_user_id()
        if creator_id != user_id:
            await message.reply_text(
                'only poll creator is allowed to whitelist chats '
                'for open user registration'
            )
            return False

        try:
            PollVoters.get(poll_id=poll_id, user_id=user_id)
            await message.reply_text(f'User #{user_id} already registered')
            return False
        except PollVoters.DoesNotExist:
            pass

        register_result = self._register_user_id(
            poll_id=poll_id, user_id=user_id,
            ignore_voter_limit=False, from_whitelist=False
        )

        if register_result.is_err():
            err: UserRegistrationStatus = register_result.err_value
            assert isinstance(err, UserRegistrationStatus)
            response_text = self._reg_status_to_msg(err, poll_id)
            await message.reply_text(response_text)
            return False

        await message.reply_text(f'User #{target_user_tele_id} registered')
        return True

    async def whitelist_chat_registration(
        self, update: ModifiedTeleUpdate, *_, **__
    ):
        return await self.set_chat_registration_status(
            update=update, whitelist=True
        )

    async def blacklist_chat_registration(
        self, update: ModifiedTeleUpdate, *_, **__
    ):
        return await self.set_chat_registration_status(
            update=update, whitelist=False
        )

    async def set_chat_registration_status(
        self, update: ModifiedTeleUpdate, whitelist: bool
    ) -> bool:
        message = update.message
        tele_user: TeleUser | None = message.from_user

        extract_poll_id_result = self.extract_poll_id(update)
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

    async def view_votes(self, update: ModifiedTeleUpdate, *_, **__):
        message: Message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()
        tele_user: TeleUser | None = update.message.from_user
        user_tele_id = tele_user.id

        try:
            user = Users.build_from_fields(tele_id=user_tele_id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        user_id = user.get_user_id()
        # check if voter is part of the poll
        get_poll_closed_result = self.get_poll_closed(poll_id)
        if get_poll_closed_result.is_err():
            error_message = get_poll_closed_result.err()
            await error_message.call(message.reply_text)
            return False

        has_poll_access = self.has_access_to_poll_id(
            poll_id, user_id, username=tele_user.username
        )
        if not has_poll_access:
            await message.reply_text(f'You have no access to poll {poll_id}')
            return False

        # get poll options in ascending order
        poll_option_rows = PollOptions.select().where(
            PollOptions.poll == poll_id
        ).order_by(PollOptions.option_number)

        # map poll option ids to their option ranking numbers
        # (option number is the position of the option in the poll)
        option_index_map = {}
        for poll_option_row in poll_option_rows:
            option_index_map[poll_option_row.id] = (
                poll_option_row.option_number
            )

        relevant_voters = PollVoters.select(PollVoters.id).where(
            PollVoters.poll == poll_id
        )
        # TODO: is this or the join query faster?
        vote_rows = VoteRankings.select().where(
            VoteRankings.poll_voter.in_(relevant_voters)
        ).order_by(
            VoteRankings.option, VoteRankings.ranking
        )
        """
        vote_rows = VoteRankings.select().join(
            PollVoters, on=(PollVoters.id == VoteRankings.poll_voter_id)
        ).where(
            PollVoters.poll_id == poll_id
        ).order_by(
            VoteRankings.option_id, VoteRankings.ranking
        )
        """

        vote_sequence_map: Dict[int, Dict[int, int]] = {}
        for vote_row in vote_rows:
            """
            Maps voters to their ranked vote
            Each ranked vote is stored as a dictionary
            mapping their vote ranking to a vote_value
            Each vote_value is either a poll option_id 
            (which is always a positive number), 
            or either of the <abstain> or <withhold> special votes
            (which are represented as negative numbers -1 and -2)
            """
            voter_id: int = vote_row.poll_voter.id
            assert isinstance(voter_id, int)

            if voter_id not in vote_sequence_map:
                vote_sequence_map[voter_id] = {}

            option_id_row = vote_row.option

            if option_id_row is None:
                vote_value = vote_row.special_value
                assert vote_value < 0
            else:
                vote_value = option_id_row.id
                assert vote_value > 0

            ranking = int(vote_row.ranking)
            ranking_map = vote_sequence_map[voter_id]
            ranking_map[ranking] = vote_value

        ranking_message = ''
        for voter_id in vote_sequence_map:
            # format vote sequence map into string rankings
            ranking_map = vote_sequence_map[voter_id]
            ranking_nos = sorted(ranking_map.keys())
            sorted_option_nos = [
                ranking_map[ranking] for ranking in ranking_nos
            ]

            # print('SORT-NOS', sorted_option_nos)
            str_rankings = []

            for vote_value in sorted_option_nos:
                if vote_value > 0:
                    option_id = vote_value
                    option_rank_no = option_index_map[option_id]
                    str_rankings.append(str(option_rank_no))
                else:
                    str_rankings.append(
                        SpecialVotes(vote_value).to_string()
                    )

            rankings_str = ' > '.join(str_rankings).strip()
            ranking_message += rankings_str + '\n'

        ranking_message = ranking_message.strip()
        await message.reply_text(f'votes recorded:\n{ranking_message}')

    async def unclose_poll_admin(self, update, *_, **__):
        await self._set_poll_status(update, False)

    async def close_poll_admin(self, update, *_, **__):
        await self._set_poll_status(update, True)

    @admin_only
    async def _set_poll_status(self, update: ModifiedTeleUpdate, closed=True):
        assert isinstance(update, ModifiedTeleUpdate)
        message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()

        with db.atomic():
            PollWinners.delete().where(
                PollWinners.poll == poll_id
            ).execute()
            # remove cached result for poll winner
            Polls.update({Polls.closed: closed}).where(
                Polls.id == poll_id
            ).execute()

        await message.reply_text(f'poll {poll_id} has been unclosed')

    @admin_only
    async def lookup_from_username_admin(
        self, update: ModifiedTeleUpdate, *_, **__
    ):
        """
        /lookup_from_username_admin {username}
        Looks up user_ids for users with a matching username
        """
        assert isinstance(update, ModifiedTeleUpdate)
        message = update.message
        raw_text = message.text.strip()

        try:
            username = raw_text[raw_text.index(' '):].strip()
        except ValueError:
            await message.reply_text("username not found")
            return False

        user_tele_ids = self.resolve_username_to_user_tele_ids(username)
        id_strings = ' '.join([f'#{tele_id}' for tele_id in user_tele_ids])
        await message.reply_text(textwrap.dedent(f"""
            matching user_ids for username [{username}]:
            {id_strings}
        """))

    @admin_only
    async def insert_user_admin(self, update: ModifiedTeleUpdate, *_, **__):
        """
        Inserts a user with the given user_id and username into
        the Users table
        """
        message = update.message
        raw_text = message.text.strip()

        if ' ' not in raw_text:
            await message.reply_text('Arguments not specified')
            return False

        cmd_arguments = raw_text[raw_text.index(' ') + 1:]
        # <user_id> <username> <--force (optional)>
        args_pattern = r"^([1-9]\d*)\s+(@?[a-zA-Z0-9_]+)\s?(--force)?$"
        args_regex = re.compile(args_pattern)
        match = args_regex.search(cmd_arguments)

        if match is None:
            await message.reply_text(f'Invalid arguments {[cmd_arguments]}')
            return False

        capture_groups = match.groups()
        tele_id = int(capture_groups[0])
        username: str = capture_groups[1]
        force: bool = capture_groups[2] is not None
        if username.startswith('@'): username = username[1:]
        assert len(username) >= 1

        if not force:
            user, created = Users.build_from_fields(
                tele_id=tele_id, username=username
            ).get_or_create()

            if created:
                await message.reply_text(
                    f'User with tele_id {tele_id} and username '
                    f'{username} created'
                )
            else:
                await message.reply_text(
                    'User already exists, use --force to '
                    'override existing entry'
                )
        else:
            Users.build_from_fields(
                tele_id=tele_id, username=username
            ).insert().on_conflict(
                preserve=[Users.tele_id],
                update={Users.username: username}
            ).execute()

            await message.reply_text(
                f'User with tele_id {tele_id} and username '
                f'{username} replaced'
            )

    @admin_only
    async def lookup_from_username_admin(
        self, update: ModifiedTeleUpdate, *_, **__
    ):
        """
        Looks up user_ids for users with a matching username
        """
        assert isinstance(update, ModifiedTeleUpdate)
        message = update.message
        raw_text = message.text.strip()

        try:
            username = raw_text[raw_text.index(' '):].strip()
        except ValueError:
            await message.reply_text("username not found")
            return False

        matching_users = Users.select().where(Users.username == username)
        user_tele_ids = [user.tele_id for user in matching_users]
        await message.reply_text(textwrap.dedent(f"""
            matching user_tele_ids for username [{username}]:
            {' '.join([f'#{tele_id}' for tele_id in user_tele_ids])}
        """))

    @admin_only
    async def insert_user_admin(self, update: ModifiedTeleUpdate, *_, **__):
        """
        Inserts a user with the given user_id and username into
        the Users table
        """
        message = update.message
        raw_text = message.text.strip()

        if ' ' not in raw_text:
            await message.reply_text('Arguments not specified')
            return False

        cmd_arguments = raw_text[raw_text.index(' ')+1:]
        # <user_id> <username> <--force (optional)>
        args_pattern = r"^([1-9]\d*)\s+(@?[a-zA-Z0-9_]+)\s?(--force)?$"
        args_regex = re.compile(args_pattern)
        match = args_regex.search(cmd_arguments)

        if match is None:
            await message.reply_text(f'Invalid arguments {[cmd_arguments]}')
            return False

        capture_groups = match.groups()
        tele_id = int(capture_groups[0])
        username: str = capture_groups[1]
        force: bool = capture_groups[2] is not None
        if username.startswith('@'): username = username[1:]
        assert len(username) >= 1

        if not force:
            user, created = Users.build_from_fields(
                tele_id=tele_id, username=username
            ).get_or_create()

            if created:
                await message.reply_text(
                    f'User with user_id {tele_id} and username '
                    f'{username} created'
                )
            else:
                await message.reply_text(
                    'User already exists, use --force to '
                    'override existing entry'
                )
        else:
            Users.build_from_fields(
                tele_id=tele_id, username=username
            ).insert().on_conflict(
                preserve=[Users.id],
                update={Users.username: username}
            ).execute()

            await message.reply_text(
                f'User with user_id {tele_id} and username '
                f'{username} replaced'
            )

    async def view_poll(self, update, context: ContextTypes.DEFAULT_TYPE):
        """
        example:
        /view_poll 3
        """
        message = update.message
        tele_user: TeleUser | None = update.message.from_user
        if tele_user is None:
            await message.reply_text(f'UNEXPECTED ERROR: NO TELE USER')
            return False

        extract_result = self.extract_poll_id(update)
        user_tele_id = tele_user.id

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        try:
            user = Users.build_from_fields(tele_id=user_tele_id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        user_id = user.get_user_id()
        poll_id = extract_result.unwrap()
        view_poll_result = self.get_poll_message(
            poll_id=poll_id, user_id=user_id,
            bot_username=context.bot.username,
            username=user.username
        )

        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            await error_message.call(message.reply_text)
            return False

        fetch_poll_result = self.fetch_poll(poll_id)
        if fetch_poll_result.is_err():
            error_message = fetch_poll_result.err()
            await error_message.call(message.reply)
            return False

        poll = fetch_poll_result.unwrap()
        chat_type = update.message.chat.type
        reply_markup = None

        if chat_type == 'private':
            # create vote button for reply message
            vote_markup_data = self.build_private_vote_markup(
                poll_id=poll_id, tele_user=tele_user
            )
            reply_markup = ReplyKeyboardMarkup(vote_markup_data)
        elif poll.open_registration:
            vote_markup_data = self.build_group_vote_markup(
                poll_id=poll_id
            )
            reply_markup = InlineKeyboardMarkup(vote_markup_data)

        poll_message = view_poll_result.unwrap()
        await message.reply_text(poll_message.text, reply_markup=reply_markup)
        return True

    @staticmethod
    async def view_all_polls(update: ModifiedTeleUpdate, *_, **__):
        # TODO: show voted / voters count + open / close status for each poll
        message: Message = update.message
        tele_user: TeleUser = update.message.from_user

        user_tele_id = tele_user.id
        if user_tele_id is None:
            await message.reply_text("user not found")
            return False

        assert isinstance(user_tele_id, int)

        try:
            user = Users.build_from_fields(tele_id=user_tele_id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        user_id = user.get_user_id()
        polls = Polls.select().where(Polls.creator == user_id)
        poll_descriptions = []

        if len(polls) == 0:
            await message.reply_text("No polls found")
            return False

        for poll in polls:
            poll_descriptions.append(
                f'#{poll.id}: {poll.desc}'
            )

        await message.reply_text(
            'Polls found:\n' + '\n'.join(poll_descriptions)
        )

    async def vote_and_report(
        self, raw_text: str, user_tele_id: int, message: Message,
        username: Optional[str]
    ):
        vote_result = self._vote_for_poll(
            raw_text=raw_text, user_tele_id=user_tele_id,
            username=username
        )

        if vote_result.is_err():
            error_message = vote_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = vote_result.unwrap()
        await self.send_post_vote_reply(
            message=message, poll_id=poll_id
        )

    @classmethod
    def read_vote_count(cls, poll_id: int) -> Result[int, MessageBuilder]:
        # returns all registered voters who have cast a vote
        fetch_poll_result = cls.fetch_poll(poll_id)

        if fetch_poll_result.is_err():
            return fetch_poll_result

        poll = fetch_poll_result.unwrap()
        return Ok(poll.num_votes)

    async def close_poll(self, update, *_, **__):
        message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()
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
                'only poll creator is allowed to close poll'
            )
            return False

        poll.closed = True
        poll.save()

        await message.reply_text(f'poll {poll_id} closed')
        winning_option_id, get_status = await self.get_poll_winner(poll_id)

        if get_status == GetPollWinnerStatus.COMPUTING:
            return await message.reply_text(textwrap.dedent(f"""
                Poll winner computation in progress
                Please check again later
             """))
        elif winning_option_id is not None:
            winning_options = PollOptions.select().where(
                PollOptions.id == winning_option_id
            )

            option_name = winning_options[0].option_name
            return await message.reply_text(f'Poll winner is: {option_name}')
        else:
            return await message.reply_text('Poll has no winner')

    @admin_only
    async def vote_for_poll_admin(self, update: ModifiedTeleUpdate, *_, **__):
        """
        telegram command formats:
        /vote_admin {username} {poll_id}: {option_1} > ... > {option_n}
        /vote_admin {username} {poll_id} {option_1} > ... > {option_n}
        examples:
        /vote 3: 1 > 2 > 3
        /vote 3 1 > 2 > 3
        """
        # vote for someone else
        message: Message = update.message
        raw_text = message.text.strip()

        if ' ' not in raw_text:
            await message.reply_text('no user specified')
            return False

        raw_text = raw_text[raw_text.index(' ') + 1:].strip()
        if ' ' not in raw_text:
            await message.reply_text('no poll_id specified (admin)')
            return False

        # @name#user_id or #user_id or @name, followed by space or colon
        name_or_id_pattern = re.compile(
            r"^("
            r"(@[a-zA-Z0-9]+#[0-9]+)"
            r"|"
            r"^((@[a-zA-Z0-9]+)|(#[0-9]+))"
            r")[ :]"
        )
        matches = name_or_id_pattern.match(raw_text)
        if matches is None:
            await message.reply_text(f'bad name or id {raw_text}')
            return False

        username_or_id: str = matches.group(0)
        if username_or_id is None:
            await message.reply_text("Unexpected parsing error")
            return False

        assert isinstance(username_or_id, str)
        username_or_id = username_or_id.strip()
        # raw_text = raw_text[raw_text.index(' ')+1:].strip()
        # print('RAW', [raw_text])

        if username_or_id.startswith('@'):
            # resolve telegram user_id by username
            username: str = username_or_id[1:]
            # print('USER_BEEF', [username])
            tele_id: int | EmptyField = Empty

            if '#' in username:
                username, raw_tele_id = username.split('#')
                tele_id = int(raw_tele_id)

            user_query = Users.build_from_fields(
                tele_id=tele_id, username=username
            )
            matching_users = user_query.select()

            if len(matching_users) > 1:
                user_tele_ids = [
                    user_entry.tele_id for user_entry in matching_users
                ]
                await message.reply_text(
                    f'multiple users with same username: {user_tele_ids}'
                )
                return False
            elif len(matching_users) == 0:
                if tele_id is Empty:
                    # cannot create user entry if only username was specified
                    await message.reply_text(
                        'No users with matching username found'
                    )
                    return False
                else:
                    # create user entry if username and tele_id are specified
                    user_db_entry, _ = user_query.get_or_create()
            else:
                assert len(matching_users) == 1
                user_db_entry = matching_users[0]

            user_tele_id = user_db_entry.tele_id

        elif username_or_id.startswith('#'):
            # try to parse user_id directly
            raw_tele_id = username_or_id[1:].strip()
            if ID_PATTERN.match(raw_tele_id) is None:
                await message.reply_text(f'invalid user id: {raw_tele_id}')
                return False

            user_tele_id = int(raw_tele_id)
            
            try:
                user = Users.build_from_fields(tele_id=user_tele_id).get()
            except Users.DoesNotExist:
                await message.reply_text(f'invalid user id: {user_tele_id}')
                return False

            username = user.username
        else:
            await message.reply_text(
                f'@username or #user_id or @username#user_id required'
            )
            return False

        if ' ' not in raw_text:
            await message.reply_text('invalid format (admin)')
            return False

        # print('CHAT_USERNAME', [user_id, username])
        # raw_text = raw_text[raw_text.index(' ')+1:].strip()
        # print('RAW', [raw_text])

        await self.vote_and_report(
            raw_text, user_tele_id=user_tele_id, message=message,
            username=username
        )

    async def vote_for_poll(self, update, *_, **__):
        """
        telegram command formats
        /vote {poll_id}: {option_1} > {option_2} > ... > {option_n}
        /vote {poll_id} {option_1} > {option_2} > ... > {option_n}
        example:
        /vote 3: 1 > 2 > 3
        /vote 3 1 > 2 > 3
        """
        message = update.message
        raw_text = message.text.strip()
        tele_user: TeleUser | None = update.message.from_user

        try:
            Users.build_from_fields(tele_id=tele_user.id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        username = tele_user.username
        user_tele_id = tele_user.id

        await self.vote_and_report(
            raw_text, user_tele_id, message, username=username
        )

    def _vote_for_poll(
        self, raw_text: str, user_tele_id: int, username: Optional[str]
    ) -> Result[int, MessageBuilder]:
        """
        telegram command format
        /vote {poll_id}: {option_1} > {option_2} > ... > {option_n}
        /vote {poll_id} {option_1} > {option_2} > ... > {option_n}
        example:
        /vote 3: 1 > 2 > 3
        /vote 3 1 > 2 > 3
        """
        error_message = MessageBuilder()
        # print('RAW_VOTE_TEXT', [raw_text, user_id])
        if ' ' not in raw_text:
            error_message.add('no poll id specified')
            return Err(error_message)

        unpack_result = self.unpack_rankings_and_poll_id(raw_text)

        if unpack_result.is_err():
            assert isinstance(unpack_result, Err)
            return unpack_result

        unpacked_result = unpack_result.unwrap()
        poll_id: int = unpacked_result[0]
        rankings: List[int] = unpacked_result[1]

        # print('PRE_REGISTER')
        return self.register_vote(
            poll_id=poll_id, rankings=rankings,
            user_tele_id=user_tele_id, username=username
        )

    @staticmethod
    def parse_ranking(raw_ranking: str) -> int:
        raw_ranking = raw_ranking.strip()

        try:
            special_ranking = SpecialVotes.from_string(raw_ranking)
            assert special_ranking.value < 0
            return special_ranking.value
        except ValueError:
            ranking = int(raw_ranking)
            assert ranking > 0
            return ranking

    @staticmethod
    def stringify_ranking(ranking_no: int) -> str:
        if ranking_no > 0:
            return str(ranking_no)
        else:
            return SpecialVotes(ranking_no).to_string()

    @classmethod
    def unpack_rankings_and_poll_id(
        cls, raw_text: str
    ) -> Result[Tuple[int, List[int]], MessageBuilder]:
        """
        raw_text format:
        {command} {poll_id}: {choice_1} > {choice_2} > ... > {choice_n}
        """
        # print("RAW_TEXT", raw_text)
        error_message = MessageBuilder()
        # remove starting command from raw_text
        raw_arguments = raw_text[raw_text.index(' '):].strip()

        r"""
        catches input of format:
        {poll_id}: {choice_1} > {choice_2} > ... > {choice_n}
        {poll_id} {choice_1} > {choice_2} > ... > {choice_n}

        regex breakdown:
        ^ -> start of string
        ^[0-9]+:*\s+ -> poll_id, optional colon, and space 
        (\s*[1-9]+0*\s*>)* -> ranking number (>0) then arrow
        \s*([0-9]+|withhold|abstain) -> final ranking number or special vote
        $ -> end of string        
        """
        # print('RAW_ARGS', [raw_arguments])
        pattern1 = r'^[0-9]+:?\s+(\s*[1-9]+0*\s*>)*\s*([0-9]+|{}|{})$'.format(
            *SpecialVotes.get_str_values()
        )
        r"""
        catches input of format:
        {poll_id} {choice_1} {choice_2} ... {choice_n}

        regex breakdown:
        ^ -> start of string
        ([0-9]+):*\s* -> poll_id, optional colon
        ([1-9]+0*\s+)* -> ranking number (>0) then space
        ([0-9]+|withhold|abstain) -> final ranking number or special vote
        $ -> end of string        
        """
        pattern2 = r'^([0-9]+):?\s*([1-9]+0*\s+)*([0-9]+|{}|{})$'.format(
            *SpecialVotes.get_str_values()
        )

        pattern_match1 = re.match(pattern1, raw_arguments)
        pattern_match2 = re.match(pattern2, raw_arguments)
        # print("P1P2", pattern1, pattern2)

        if pattern_match1:
            raw_arguments = raw_arguments.replace(':', '')
            separator_index = raw_arguments.index(' ')
            raw_poll_id = int(raw_arguments[:separator_index])
            raw_votes = raw_arguments[separator_index:].strip()
            rankings = [
                cls.parse_ranking(ranking)
                for ranking in raw_votes.split('>')
            ]
        elif pattern_match2:
            raw_arguments = raw_arguments.replace(':', '')
            raw_arguments = re.sub(r'\s+', ' ', raw_arguments)
            raw_arguments_arr = raw_arguments.split(' ')
            raw_poll_id = int(raw_arguments_arr[0])
            raw_votes = raw_arguments_arr[1:]
            rankings = [
                cls.parse_ranking(ranking)
                for ranking in raw_votes
            ]
        else:
            error_message.add('input format is invalid')
            return Err(error_message)

        validate_result = cls.validate_rankings(rankings)
        if validate_result.is_err():
            return validate_result

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            error_message.add(f'invalid poll id: {raw_arguments}')
            return Err(error_message)

        return Ok((poll_id, rankings))

    @staticmethod
    async def show_about(update: ModifiedTeleUpdate, *_, **__):
        message: Message = update.message
        await message.reply_text(textwrap.dedent(f"""
            Version {__VERSION__}
            The source code for this bot can be found at:
            https://github.com/milselarch/RCV-tele-bot
            Join the feedback and discussion group at: 
            https://t.me/+fs0WPn1pfmYxNjg1
        """))

    @classmethod
    async def delete_poll(cls, update: ModifiedTeleUpdate, *_, **__):
        message: Message = update.message
        tele_user = message.from_user
        user_tele_id = tele_user.id

        try:
            user = Users.build_from_fields(tele_id=user_tele_id).get()
            user_id = user.get_user_id()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        if user_tele_id is None:
            await message.reply_text("user not found")
            return False

        raw_text = message.text.strip()
        if ' ' not in raw_text:
            await message.reply_text('no poll ID specified')
            return False

        raw_command_args = raw_text[raw_text.index(' ') + 1:].strip()
        print(f'{raw_command_args=}')
        # captures [int id] [optional --force or —force]
        args_pattern = r'^([0-9]+)\s*(\s(?:--|—)force)?$'
        matches = re.match(args_pattern, raw_command_args)
        if matches is None:
            await message.reply_text('invalid command arguments')
            return False

        match_groups = matches.groups()

        try:
            raw_poll_id, raw_force_arg = match_groups
        except ValueError:
            await message.reply_text('Unexpected error while parsing command')
            return False

        force_delete = raw_force_arg is not None

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            await message.reply_text(f'invalid poll ID: {raw_command_args}')
            return False

        poll_query = (Polls.id == poll_id) & (Polls.creator == user_id)
        poll = Polls.get_or_none(poll_query)

        if poll is None:
            await message.reply_text(f'poll #{poll_id} not found')
            return False
        elif force_delete:
            Polls.delete().where(poll_query).execute()
            await message.reply_text(f'Poll #{poll_id} ({poll.desc}) deleted')
            return True
        elif not poll.closed:
            await message.reply_text(f'poll #{poll_id} must be closed first')
            return False

        callback_data = json.dumps(cls.kwargify(
            poll_id=poll_id, command=str(CallbackCommands.DELETE),
            stamp=int(time.time())
        ))
        markup_layout = [[InlineKeyboardButton(
            text=f'Delete poll #{poll_id}', callback_data=callback_data
        )]]

        reply_markup = InlineKeyboardMarkup(markup_layout)
        await message.reply_text(
            f'Confirm poll #{poll_id} deletion',
            reply_markup=reply_markup
        )
        return True

    @track_errors
    async def delete_account(self, update: ModifiedTeleUpdate, *_, **__):
        deletion_token_res = self.get_raw_command_args(update)

        if deletion_token_res.is_err():
            # deletion token not provided, send deletion instructions
            delete_token = self.generate_delete_token(update.user)
            return await update.message.reply_text(textwrap.dedent(f"""
                Deleting your account will accomplish the following:
                - all polls you've created will be deleted
                - all votes you've cast for any ongoing polls
                  will be deleted, and you will be deregistered
                  from these ongoing polls
                - all votes you've cast for any closed polls will
                  be decoupled from your user account
                - your user account will be marked as deleted and you
                  will not be able to create new polls or vote using
                  your account moving forward
                - your user account will be removed from the database
                  28 days after being marked for deletion 
                
                Confirm account deletion by running the delete command 
                with the provided deletion token:
                ——————————————————
                /delete_account {delete_token}
            """))

        deletion_token = deletion_token_res.unwrap()
        match_pattern = f'^[0-9A-F]+:[0-9A-F]+$'
        if re.match(match_pattern, deletion_token) is None:
            return await update.message.reply_text('Invalid deletion token')

        user: Users = update.user
        user_id = user.get_user_id()
        hex_stamp, short_hash = deletion_token.split(':')
        deletion_stamp = int(hex_stamp, 16)
        validation_result = self.validate_delete_token(
            user=user, stamp=deletion_stamp, short_hash=short_hash
        )
        if validation_result.is_err():
            err_message = validation_result.err()
            return await update.message.reply_text(err_message)

        try:
            with db.atomic():
                # delete all polls created by the user
                Polls.delete().where(Polls.creator == user_id).execute()
                user.deleted_at = Datetime.now()  # mark as deleted
                user.save()

                poll_registrations: Iterable[PollVoters] = (
                    PollVoters.select().where(PollVoters.user == user_id)
                )
                for poll_registration in poll_registrations:
                    poll: Polls = poll_registration.poll

                    if poll.closed:
                        # decouple poll voter from user
                        poll_registration.user = None
                        poll_registration.save()
                    else:
                        # delete poll voter and increment deleted voters count
                        poll.deleted_voters += 1
                        poll_registration.delete_instance()
                        poll.save()

        except Exception as e:
            await update.message.reply_text(
                'Unexpected error occurred during account deletion'
            )
            raise e

        return await update.message.reply_text(
            'Account deleted successfully'
        )

    @staticmethod
    async def show_help(update: ModifiedTeleUpdate, *_, **__):
        message: Message = update.message
        await message.reply_text(textwrap.dedent("""
           /start - start bot
           /user_details - shows your username and user id
           ——————————————————
           /create_poll @username_1 @username_2 ... @username_n:
           poll title
           poll option 1
           poll option 2
           ...
           poll option m   

           Creates a new poll
           ——————————————————
           /create_group_poll @username_1 @username_2 ... @username_n:
           poll title
           poll option 1
           poll option 2
           ...
           poll option m
           
           Creates a new poll that chat members can self-register for
           ——————————————————
           /register_user_id {poll_id} {user_id}
           Registers a user by user_id for a poll
           ——————————————————
           /whitelist_chat_registration {poll_id}
           Whitelists the current chat so that chat members can self-register
           for the poll specified by poll_id within the chat group
           ——————————————————
           /blacklist_chat_registration {poll_id}
           Blacklists the current chat so that chat members cannot 
           self-register for the poll specified by poll_id within the chat
           group
           ——————————————————
           /view_poll {poll_id} - shows poll details given poll_id
           ——————————————————
           /vote {poll_id}: {option_1} > {option_2} > ... > {option_n} 
           /vote {poll_id} {option_1} > {option_2} > ... > {option_n} 
           /vote {poll_id} {option_1} {option_2} ... {option_n} 

           Last option can also accept 2 special values, withhold and abstain:
               > Vote withhold if you want to vote for none of the options
               > Vote abstain if you want to remove yourself from the poll 

           Vote for the poll with the specified poll_id
           requires that the user is one of the registered 
           voters of the poll
           ——————————————————
           /poll_results {poll_id}
           Returns poll results if the poll has been closed
           ——————————————————
           /has_voted {poll_id} 
           Tells you if you've voted for the poll with the 
           specified poll_id
           ——————————————————
           /close_poll {poll_id}
           Close the poll with the specified poll_id
           Note that only the creator of the poll is allowed 
           to issue this command to close the poll
           ——————————————————
           /view_votes {poll_id}
           View all the votes entered for the poll 
           with the specified poll_id. 
           This can only be done after the poll has been closed first
           ——————————————————
           /view_voters {poll_id}
           Show which voters have voted and which have not
           ——————————————————
           /about - view miscellaneous information about the bot
           /view_polls - view all polls created by you
           ——————————————————
           /delete_poll {poll_id} - delete poll by poll_id
           Use /delete_poll --force to force delete the poll without 
           confirmation, regardless of whether poll is open or closed
           ——————————————————
           /delete_account
           /delete_account {deletion_token}
           Delete your user account (this cannot be undone)
           ——————————————————
           /help - view commands available to the bot
           """))

    async def view_poll_voters(self, update, *_, **__):
        """
        /view_voters {poll_id}
        :param update:
        :param _:
        :param __:
        :return:
        """
        message: Message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()
        tele_user: TeleUser | None = message.from_user
        if tele_user is None:
            await message.reply_text('UNEXPECTED ERROR: USER NOT FOUND')
            return False

        user_tele_id = tele_user.id

        try:
            user = Users.build_from_fields(tele_id=user_tele_id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        user_id = user.get_user_id()
        # check if voter is part of the poll
        has_poll_access = self.has_access_to_poll_id(
            poll_id, user_id, username=tele_user.username
        )
        if not has_poll_access:
            await message.reply_text(f'You have no access to poll {poll_id}')
            return False

        read_vote_count_result = self.read_vote_count(poll_id)
        if read_vote_count_result.is_err():
            error_message = read_vote_count_result.err()
            await error_message.call(message.reply_text)
            return False

        vote_count = read_vote_count_result.unwrap()
        poll_voters: Iterable[PollVoters] = PollVoters.select().join(
            Users, on=(PollVoters.user == Users.id),
            join_type=JOIN.LEFT_OUTER
        ).where(
            PollVoters.poll == poll_id
        )

        if vote_count >= MAX_DISPLAY_VOTE_COUNT:
            await message.reply_text(
                f'Can only display voters when vote count is '
                f'under {MAX_DISPLAY_VOTE_COUNT}'
            )
            return False

        voted_usernames, not_voted_usernames = [], []
        recorded_user_ids: set[int] = set()

        for voter in poll_voters:
            # TODO: check if user has been deleted
            username: Optional[str] = voter.user.username
            voter_user = voter.get_voter_user()
            user_tele_id = voter_user.get_tele_id()
            recorded_user_ids.add(user_tele_id)

            if username is None:
                display_name: str = f'#{user_tele_id}'
            else:
                display_name: str = username

            if voter.voted:
                voted_usernames.append(display_name)
            else:
                not_voted_usernames.append(display_name)

        whitelisted_usernames = UsernameWhitelist.select().where(
            UsernameWhitelist.poll == poll_id
        )

        for whitelist_entry in whitelisted_usernames:
            optional_user = whitelist_entry.user
            username: str = whitelist_entry.username

            if optional_user is not None:
                user_tele_id = whitelist_entry.user.get_tele_id()
                if user_tele_id not in recorded_user_ids:
                    not_voted_usernames.append(username)
            else:
                not_voted_usernames.append(username)

        await message.reply_text(textwrap.dedent(f"""
            voted:
            {' '.join(voted_usernames)}
            not voted:
            {' '.join(not_voted_usernames)}
        """))

    async def fetch_poll_results(self, update, *_, **__):
        """
        /poll_results 5
        :param update:
        :param _:
        :param __:
        :return:
        """
        message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()
        tele_user: TeleUser | None = message.from_user
        user_tele_id = tele_user.id

        try:
            user = Users.build_from_fields(tele_id=user_tele_id).get()
        except Users.DoesNotExist:
            await message.reply_text(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return False

        user_id = user.get_user_id()
        # check if voter is part of the poll
        has_poll_access = self.has_access_to_poll_id(
            poll_id, user_id, username=tele_user.username
        )
        if not has_poll_access:
            await message.reply_text(f'You have no access to poll {poll_id}')
            return False

        get_poll_closed_result = self.get_poll_closed(poll_id)
        if get_poll_closed_result.is_err():
            error_message = get_poll_closed_result.err()
            await error_message.call(message.reply_text)

        winning_option_id, get_status = await self.get_poll_winner(poll_id)

        if get_status == GetPollWinnerStatus.COMPUTING:
            await message.reply_text(textwrap.dedent(f"""
                Poll winner computation in progress
                Please check again later
            """))
        elif winning_option_id is not None:
            winning_options = PollOptions.select().where(
                PollOptions.id == winning_option_id
            )

            option_name = winning_options[0].option_name
            return await message.reply_text(f'Poll winner is: {option_name}')
        else:
            return await message.reply_text('Poll has no winner')

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

    @staticmethod
    def get_raw_command_args(
        update: ModifiedTeleUpdate
    ) -> Result[str, MessageBuilder]:
        message: telegram.Message = update.message
        error_message = MessageBuilder()

        if message.text is None:
            error_message.add('no text found in message')
            return Err(error_message)

        raw_text = message.text.strip()
        if ' ' not in raw_text:
            error_message.add('no poll id specified')
            return Err(error_message)

        raw_command_args = raw_text[raw_text.index(' '):].strip()
        return Ok(raw_command_args)

    @classmethod
    def extract_poll_id(
        cls, update: ModifiedTeleUpdate
    ) -> Result[int, MessageBuilder]:
        raw_args_res = cls.get_raw_command_args(update)
        if raw_args_res.is_err():
            return raw_args_res

        raw_poll_id = raw_args_res.unwrap()
        error_message = MessageBuilder()

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            error_message.add(f'invalid poll id: {raw_poll_id}')
            return Err(error_message)

        return Ok(poll_id)


if __name__ == '__main__':
    rcv_bot = RankedChoiceBot()
    rcv_bot.start_bot()
