from __future__ import annotations

import json
import logging
import os
import time
import textwrap
import telegram
import asyncio
import re

from peewee import JOIN
from result import Ok, Result

from handlers.inline_keyboard_handlers import InlineKeyboardHandlers
from handlers.payment_handlers import PaymentHandlers
from handlers.start_handlers import start_handlers
from helpers.commands import Command
from helpers.constants import BLANK_ID
from helpers.locks_manager import PollsLockManager
from helpers.message_buillder import MessageBuilder
from logging import handlers as log_handlers
from datetime import datetime

from helpers import strings
from helpers.rcv_tally import RCVTally
from helpers.redis_cache_manager import GetPollWinnerStatus
from tele_helpers import ModifiedTeleUpdate
from helpers.special_votes import SpecialVotes
from bot_middleware import track_errors, admin_only
from database.database import UserID, CallbackContextState
from database.db_helpers import EmptyField, Empty
from handlers.chat_context_handlers import context_handlers, ClosePollContextHandler
from helpers import constants

from telegram import (
    Message, ReplyKeyboardMarkup, InlineKeyboardMarkup,
    User as TeleUser, Update as BaseTeleUpdate, Bot
)
from telegram.ext import (
    ContextTypes, filters, CallbackContext, Application
)
from typing import (
    List, Dict, Optional, Sequence, Iterable
)

from helpers.strings import (
    POLL_OPTIONS_LIMIT_REACHED_TEXT, READ_SUBSCRIPTION_TIER_FAILED,
    generate_poll_created_message
)
from helpers.chat_contexts import (
    PollCreationChatContext, PollCreatorTemplate, POLL_MAX_OPTIONS,
    VoteChatContext, PaySupportChatContext, ClosePollChatContext,
    EditPollTitleChatContext
)
from database import (
    Users, Polls, PollVoters, UsernameWhitelist,
    PollOptions, VoteRankings, db, ChatWhitelist, PollWinners,
    MessageContextState, Payments
)
from base_api import BaseAPI, UserRegistrationStatus, CallbackCommands
from tele_helpers import TelegramHelpers

# https://stackoverflow.com/questions/15892946/
# We should empty root.handlers before calling basicConfig() method.
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

LOG_PATH = 'logs/log_events.txt'
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
file_log_handler = log_handlers.TimedRotatingFileHandler(
    LOG_PATH, when='W6'
)
file_log_handler.setLevel(logging.WARNING)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(), file_log_handler]
)
logger = logging.getLogger(__name__)
logger.warning("<<< INITIALIZING >>>")


class RankedChoiceBot(BaseAPI):
    def __init__(self, config_path='config.yml'):
        super().__init__()
        self.config_path = config_path
        self.scheduled_processes = []
        self.payment_handlers = None

        self.webhook_url = None
        self.bot = None
        self.app = None

    @classmethod
    async def _call_polling_tasks_routine(cls):
        # TODO: write tests for this
        while True:
            await cls._call_polling_tasks_once()
            await asyncio.sleep(constants.POLLING_TASKS_INTERVAL)

    def get_bot(self) -> Bot:
        assert self.bot is not None
        return self.bot

    @classmethod
    async def _call_polling_tasks_once(cls):
        print(f'CALLING_CLEANUP @ {datetime.now()}')
        Users.prune_deleted_users(logger)
        CallbackContextState.prune_expired_contexts()
        MessageContextState.prune_expired_contexts()
        Payments.prune_expired()

    def start_bot(self):
        assert self.bot is None
        self.bot = self.create_tele_bot()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # ensure scheduled tasks don't crash before running
        # them in the background
        loop.run_until_complete(self._call_polling_tasks_once())
        loop.create_task(self._call_polling_tasks_routine())

        builder = self.create_application_builder()
        builder.concurrent_updates(constants.MAX_CONCURRENT_UPDATES)
        builder.post_init(self.post_init)

        self.app = builder.build()
        self.payment_handlers = PaymentHandlers(logger)

        commands_mapping = {
            Command.START: start_handlers.start_handler,
            Command.USER_DETAILS: self.user_details_handler,
            Command.CHAT_DETAILS: self.chat_details_handler,
            Command.CREATE_PRIVATE_POLL: self.create_poll,
            Command.CREATE_GROUP_POLL: self.create_group_poll,
            Command.REGISTER_USER_ID: self.register_user_by_tele_id,
            Command.WHITELIST_CHAT_REGISTRATION:
                self.whitelist_chat_registration,
            Command.BLACKLIST_CHAT_REGISTRATION:
                self.blacklist_chat_registration,

            Command.VIEW_POLL: self.view_poll,
            Command.VIEW_POLLS: self.view_all_polls,
            Command.VOTE: self.vote_for_poll_handler,
            Command.POLL_RESULTS: self.fetch_poll_results,
            Command.HAS_VOTED: self.has_voted,
            Command.CLOSE_POLL: self.close_poll_handler,
            Command.EDIT_POLL_TITLE: self.edit_poll_title_handler,
            Command.VIEW_VOTES: self.view_votes,
            Command.VIEW_VOTERS: self.view_poll_voters,
            Command.ABOUT: self.show_about,
            Command.DELETE_POLL: self.delete_poll,
            Command.DELETE_ACCOUNT: self.delete_account,
            Command.HELP: self.show_help,
            Command.DONE: context_handlers.complete_chat_context,
            Command.SET_MAX_VOTERS: self.payment_handlers.set_max_voters,
            Command.WHITELIST_USERNAME: self.whitelist_username,
            Command.PAY_SUPPORT: self.payment_support_handler,

            Command.VOTE_ADMIN: self.vote_for_poll_admin,
            Command.CLOSE_POLL_ADMIN: self.close_poll_admin,
            Command.UNCLOSE_POLL_ADMIN: self.unclose_poll_admin,
            Command.LOOKUP_FROM_USERNAME_ADMIN:
                self.lookup_from_username_admin,
            Command.INSERT_USER_ADMIN: self.insert_user_admin,
            Command.REFUND_ADMIN: self.refund_payment_support_handler,
            Command.ENTER_MAINTENANCE_ADMIN: self.enter_maintenance_admin,
            Command.EXIT_MAINTENANCE_ADMIN: self.exit_maintenance_admin,
            Command.SEND_MSG_ADMIN: self.send_msg_admin
        }

        # on different commands - answer in Telegram
        TelegramHelpers.register_commands(
            self.app, commands_mapping=commands_mapping
        )
        TelegramHelpers.register_pre_checkout_handler(
            self.app, self.payment_handlers.pre_checkout_callback
        )
        # catch-all to handle responses to unknown commands
        TelegramHelpers.register_message_handler(
            self.app, filters.Regex(r'^/') & filters.COMMAND,
            self.handle_unknown_command
        )
        # handle web app updates
        TelegramHelpers.register_message_handler(
            self.app, filters.StatusUpdate.WEB_APP_DATA,
            self.web_app_handler
        )
        # handle payment-related messages
        TelegramHelpers.register_message_handler(
            self.app, filters.SUCCESSFUL_PAYMENT,
            self.payment_handlers.successful_payment_callback
        )
        # catch-all to handle all other messages
        TelegramHelpers.register_message_handler(
            self.app, filters.Regex(r'.*') & filters.TEXT,
            context_handlers.handle_other_messages
        )
        inline_keyboard_handlers = InlineKeyboardHandlers(logger)
        TelegramHelpers.register_callback_handler(
            self.app, inline_keyboard_handlers.route
        )

        # self.app.add_error_handler(self.error_handler)
        self.app.run_polling(allowed_updates=BaseTeleUpdate.ALL_TYPES)
        print('<<< BOT POLLING LOOP ENDED >>>')

    async def post_init(self, _: Application):
        # print('SET COMMANDS')
        await self.get_bot().set_my_commands([(
            Command.START, 'start bot'
        ), (
            Command.USER_DETAILS, 'shows your username and user id'
        ), (
            Command.CHAT_DETAILS,  'shows chat id'
        ), (
            Command.CREATE_GROUP_POLL, 'create a new poll'
        ), (
            Command.CREATE_PRIVATE_POLL,
            'create a new poll that users cannot self register for'
        ), (
            Command.SET_MAX_VOTERS,
            'set the maximum number of voters who can vote for a poll'
        ), (
            Command.PAY_SUPPORT, 'payment support'
        ), (
            Command.REGISTER_USER_ID,
            'registers a user by user_id for a poll'
        ), (
            Command.WHITELIST_USERNAME,
            'whitelist a username for a poll'
        ), (
            Command.WHITELIST_CHAT_REGISTRATION,
            'whitelist a chat for self registration'
        ), (
            Command.BLACKLIST_CHAT_REGISTRATION,
            'removes a chat from self registration whitelist'
        ), (
            Command.VIEW_POLL, 'shows poll details given poll_id'
        ), (
            Command.VIEW_POLLS, 'shows all polls that you have created'
        ), (
            Command.VOTE, 'vote for the poll with the specified poll_id'
        ), (
            Command.POLL_RESULTS,
            'returns poll results if the poll has been closed'
        ), (
            Command.HAS_VOTED,
            "check if you've voted for the poll given the poll ID"
        ), (
            Command.CLOSE_POLL,
            'close the poll with the specified poll_id'
        ), (
            Command.EDIT_POLL_TITLE,
            'edit the title of the poll with the specified poll_id '
            '(only for polls that haven\'t been voted for)'
        ), (
            Command.VIEW_VOTES,
            'view all the votes entered for the poll'
        ), (
            Command.VIEW_VOTERS,
            'show which voters have voted and which have not'
        ), (
            Command.ABOUT, 'miscellaneous info about the bot'
        ), (
            Command.DELETE_POLL, 'delete a poll'
        ), (
            Command.DELETE_ACCOUNT, 'delete your user account'
        ), (
            Command.HELP, 'view commands available to the bot'
        ), (
            Command.DONE, 'finish creating a poll or ranked vote'
        )])

    @track_errors
    async def web_app_handler(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        # TODO: update reference poll message with latest voter count
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
            user_tele_id=user_tele_id, username=username,
            chat_id=message.chat_id
        )

        if vote_result.is_err():
            error_message = vote_result.err()
            await error_message.call(message.reply_text)
            return False

        await TelegramHelpers.send_post_vote_reply(
            message=message, poll_id=poll_id
        )

        ref_info = payload.get('ref_info', str(BLANK_ID))
        ref_hash = payload.get('ref_hash', '')
        # print('REFS', ref_info, ref_hash)
        signed_ref_info = BaseAPI.sign_data_check_string(ref_info)
        if signed_ref_info != ref_hash:
            # print('REJECT_HASH')
            return None

        # update the poll voter count in the originating poll message
        _, raw_poll_id, raw_ref_msg_id, raw_ref_chat_id = ref_info.split(':')

        poll_id = int(raw_poll_id)
        ref_msg_id = int(raw_ref_msg_id)
        ref_chat_id = int(raw_ref_chat_id)
        if (ref_msg_id == BLANK_ID) or (ref_chat_id == BLANK_ID):
            return None

        poll_info = BaseAPI.unverified_read_poll_info(poll_id=poll_id)
        return await TelegramHelpers.update_poll_message(
            poll_info=poll_info, chat_id=ref_chat_id,
            message_id=ref_msg_id, context=context,
            poll_locks_manager=PollsLockManager()
        )

    @track_errors
    async def handle_unknown_command(self, update: ModifiedTeleUpdate, _):
        await update.message.reply_text("Command not found")

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
        tele_user: TeleUser = update.message.from_user
        await update.message.reply_text(textwrap.dedent(f"""
            user id: {tele_user.id}
            username: {tele_user.username}
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
        user = update.user

        extract_poll_id_result = TelegramHelpers.extract_poll_id(update)
        if extract_poll_id_result.is_err():
            await message.reply_text('Poll ID not specified')
            return False

        user_id = user.get_user_id()
        poll_id = extract_poll_id_result.unwrap()
        is_voter = PollVoters.is_poll_voter(
            poll_id=poll_id, user_id=user_id
        )

        if not is_voter:
            await message.reply_text(
                f"You're not a voter of poll {poll_id}"
            )
            return False

        voted = self.check_has_voted(poll_id=poll_id, user_id=user_id)

        if voted:
            return await message.reply_text("you've voted already")
        else:
            return await message.reply_text("you haven't voted")

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
        user_entry: Users = update.user
        user_id = user_entry.get_user_id()
        raw_poll_creation_args = TelegramHelpers.read_raw_command_args(
            update, strip=False
        ).rstrip()

        # initiate poll creation context here
        if raw_poll_creation_args == '':
            num_user_created_polls = Polls.count_polls_created(user_id)
            subscription_tier_res = user_entry.get_subscription_tier()
            if subscription_tier_res.is_err():
                return await message.reply_text(READ_SUBSCRIPTION_TIER_FAILED)

            subscription_tier = subscription_tier_res.unwrap()
            poll_creation_limit = subscription_tier.get_max_polls()

            if num_user_created_polls >= poll_creation_limit:
                await message.reply_text(POLL_OPTIONS_LIMIT_REACHED_TEXT)
                return False

            PollCreationChatContext(
                user_id=user_entry.get_user_id(), chat_id=message.chat.id,
                max_options=POLL_MAX_OPTIONS, poll_options=[],
                open_registration=open_registration
            ).save_state()

            await message.reply_text(
                "Enter the title / question for your new poll:"
            )
            return True

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
        if ':' in all_lines[0]:
            # separate poll voters (before :) from poll title and options
            split_index = raw_poll_creation_args.index(':')
            # first part of command is all the users that are in the poll
            command_p1: str = raw_poll_creation_args[:split_index].strip()
            # second part of command is the poll question + poll options
            command_p2: str = raw_poll_creation_args[split_index+1:].strip()
        else:
            # no : on first line to separate poll voters and
            # poll title + questions
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

        poll_creator = PollCreatorTemplate(
            creator_id=creator_id, user_rows=user_rows,
            poll_user_tele_ids=poll_user_tele_ids,
            poll_question=poll_question,
            subscription_tier=subscription_tier,
            open_registration=open_registration,
            poll_options=poll_options,
            whitelisted_usernames=whitelisted_usernames,
            whitelisted_chat_ids=whitelisted_chat_ids
        )

        create_poll_res = poll_creator.save_poll_to_db()
        if create_poll_res.is_err():
            error_message = create_poll_res.err()
            await error_message.call(message.reply_text)
            return False

        new_poll: Polls = create_poll_res.unwrap()
        new_poll_id = int(new_poll.id)
        bot_username = context.bot.username

        poll_message = self.generate_poll_info(
            new_poll_id, poll_question, poll_options,
            bot_username=bot_username, closed=False,
            num_voters=poll_creator.initial_num_voters,
            max_voters=new_poll.max_voters,
            add_instructions=update.is_group_chat()
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
                poll_id=new_poll_id,
                num_options=len(poll_options)
            )
            reply_markup = InlineKeyboardMarkup(vote_markup_data)

        await message.reply_text(poll_message, reply_markup=reply_markup)
        return await message.reply_text(
            generate_poll_created_message(new_poll_id)
        )

    @classmethod
    async def whitelist_chat_registration(
        cls, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        extract_poll_id_result = TelegramHelpers.extract_poll_id(update)
        if extract_poll_id_result.is_err():
            error_message = extract_poll_id_result.err()
            await error_message.call(update.message.reply_text)
            return False

        poll_id = extract_poll_id_result.unwrap()
        return await TelegramHelpers.set_chat_registration_status(
            update, context, whitelist=True, poll_id=poll_id
        )

    @classmethod
    async def blacklist_chat_registration(
        cls, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        extract_poll_id_result = TelegramHelpers.extract_poll_id(update)
        if extract_poll_id_result.is_err():
            error_message = extract_poll_id_result.err()
            await error_message.call(update.message.reply_text)
            return False

        poll_id = extract_poll_id_result.unwrap()
        return await TelegramHelpers.set_chat_registration_status(
            update, context, whitelist=False, poll_id=poll_id
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
        r"""
        ^\S+ - command name
        \s+ - whitespace
        ([1-9]\d*) - poll_id
        s+ - whitespace
        ([1-9]\d*) - user_tele_id
        """
        pattern = re.compile(r'^\S+\s+([1-9]\d*)\s+([1-9]\d*)$')
        matches = pattern.match(raw_text)
        format_invalid_message = textwrap.dedent(f"""
            Format invalid. 
            Use /{Command.REGISTER_USER_ID} {{poll_id}} {{user_tele_id}}
        """)

        if tele_user is None:
            await message.reply_text(f'user not found')
            return False
        if matches is None:
            await message.reply_text(format_invalid_message)
            return False

        capture_groups = matches.groups()
        if len(capture_groups) != 2:
            await message.reply_text(format_invalid_message)
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

        target_user_id = target_user.get_user_id()
        current_user_id = update.user.get_user_id()
        creator_id: UserID = poll.get_creator().get_user_id()
        if creator_id != current_user_id:
            await message.reply_text(
                'only poll creator is allowed to whitelist chats '
                'for open user registration'
            )
            return False

        try:
            PollVoters.get(poll_id=poll_id, user_id=target_user_id)
            await message.reply_text(f'User #{target_user_id} already registered')
            return False
        except PollVoters.DoesNotExist:
            pass

        register_result = self.register_user_id(
            poll_id=poll_id, user_id=target_user_id,
            ignore_voter_limit=False, from_whitelist=False
        )

        if register_result.is_err():
            err: UserRegistrationStatus = register_result.err_value
            assert isinstance(err, UserRegistrationStatus)
            response_text = self.reg_status_to_msg(err, poll_id)
            await message.reply_text(response_text)
            return False

        await message.reply_text(f'User #{target_user_tele_id} registered')
        return True

    async def view_votes(self, update: ModifiedTeleUpdate, *_, **__):
        message: Message = update.message
        extract_result = TelegramHelpers.extract_poll_id(update)

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
        return await message.reply_text(f'votes recorded:\n{ranking_message}')

    async def unclose_poll_admin(self, update, *_, **__):
        await self._set_poll_status(update, False)

    async def close_poll_admin(self, update, *_, **__):
        await self._set_poll_status(update, True)

    @admin_only
    async def _set_poll_status(self, update: ModifiedTeleUpdate, closed=True):
        assert isinstance(update, ModifiedTeleUpdate)
        message = update.message
        extract_result = TelegramHelpers.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()

        with db.atomic():
            PollWinners.delete().where(
                PollWinners.poll == poll_id
            ).execute()
            # remove the cached result for the poll winner
            Polls.update({Polls.closed: closed}).where(
                Polls.id == poll_id
            ).execute()

        return await message.reply_text(
            f'poll {poll_id} has been unclosed'
        )

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
        return await message.reply_text(textwrap.dedent(f"""
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
        if username.startswith('@'):
            username = username[1:]

        assert len(username) >= 1

        if not force:
            user, created = Users.build_from_fields(
                tele_id=tele_id, username=username
            ).get_or_create()

            if created:
                return await message.reply_text(
                    f'User with tele_id {tele_id} and username '
                    f'{username} created'
                )
            else:
                return await message.reply_text(
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

            return await message.reply_text(
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
        return await message.reply_text(textwrap.dedent(f"""
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
        if username.startswith('@'):
            username = username[1:]

        assert len(username) >= 1

        if not force:
            user, created = Users.build_from_fields(
                tele_id=tele_id, username=username
            ).get_or_create()

            if created:
                return await message.reply_text(
                    f'User with user_id {tele_id} and username '
                    f'{username} created'
                )
            else:
                return await message.reply_text(
                    'User already exists, use --force to '
                    'override existing entry'
                )
        else:
            # TODO: check if update on insert conflict works with MySQL
            Users.build_from_fields(
                tele_id=tele_id, username=username
            ).insert().on_conflict(
                preserve=[Users.id],
                update={Users.username: username}
            ).execute()

            return await message.reply_text(
                f'User with user_id {tele_id} and username '
                f'{username} replaced'
            )

    @classmethod
    async def view_poll(
        cls, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        """
        example:
        /view_poll 3
        """
        message = update.message
        extract_result = TelegramHelpers.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()
        return await TelegramHelpers.view_poll_by_id(
            update, context, poll_id=poll_id
        )

    @staticmethod
    async def view_all_polls(update: ModifiedTeleUpdate, *_, **__):
        # TODO: show voted / voters count + open / close status for each poll
        # TODO: write test to check that user can only read their own polls
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

        polls = user.get_owned_polls()
        poll_descriptions = []

        if len(polls) == 0:
            await message.reply_text("No polls found")
            return False

        for poll in polls:
            poll_descriptions.append(
                f'#{poll.id}: {poll.desc}'
            )

        return await message.reply_text(
            'Polls found:\n' + '\n'.join(poll_descriptions)
        )

    @classmethod
    def read_vote_count(cls, poll_id: int) -> Result[int, MessageBuilder]:
        # returns all registered voters who have cast a vote
        fetch_poll_result = RCVTally.fetch_poll(poll_id)

        if fetch_poll_result.is_err():
            return fetch_poll_result

        poll = fetch_poll_result.unwrap()
        return Ok(poll.num_votes)

    @classmethod
    async def close_poll_handler(cls, update, *_, **__):
        message = update.message
        message_text = TelegramHelpers.read_raw_command_args(update)
        user = update.user

        if message_text == '':
            ClosePollChatContext(
                user_id=user.get_user_id(), chat_id=message.chat.id
            ).save_state()
            return await message.reply_text(
                'Enter the poll ID for the poll you want to close'
            )

        if constants.ID_PATTERN.match(message_text) is None:
            return await message.reply_text(textwrap.dedent(f"""
                Input format is invalid, try:
                /{Command.CLOSE_POLL} {{poll_id}}
            """))

        extract_result = TelegramHelpers.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()
        user_id = update.user.get_user_id()
        return await ClosePollContextHandler.close_poll(
            poll_id=poll_id, user_id=user_id,
            update=update
        )

    @classmethod
    async def edit_poll_title_handler(cls, update: ModifiedTeleUpdate, *_, **__):
        """
        Command usage:
        /edit_poll_title {poll_id} {new_title}
        /edit_poll_title {poll_id}
        /edit_poll_title
        """
        message = update.message
        user_id = update.user.get_user_id()

        new_title = ""
        message_text = TelegramHelpers.read_raw_command_args(update)
        invalid_format_text = textwrap.dedent(f"""
            Input format is invalid, try:
            /{Command.EDIT_POLL_TITLE} {{poll_id}} {{new_title}}
        """)

        if message_text == '':
            # no poll_id or new title specified
            EditPollTitleChatContext(
                user_id=user_id, chat_id=message.chat.id,
                poll_id=BLANK_ID
            ).save_state()

            return await message.reply_text(textwrap.dedent(f"""
                Enter the poll ID for the poll you want to edit:
            """))
        elif ' ' in message_text:
            # both poll_id and new title are specified
            raw_poll_id = message_text[:message_text.index(' ')].strip()
            new_title = message_text[message_text.index(' '):].strip()
            poll_id = int(raw_poll_id)
        elif constants.ID_PATTERN.match(message_text) is not None:
            # only poll_id is specified, poll_id is valid
            poll_id = int(message_text)
        else:
            # only poll_id is specified, poll_id is invalid
            return await message.reply_text(invalid_format_text)

        poll_res = Polls.get_as_creator(poll_id, user_id)
        if poll_res.is_err():
            return await message.reply_text(
                "You're not the creator of this poll"
            )

        poll = poll_res.unwrap()
        if poll.num_votes > 0:
            return await message.reply_text(
                "Cannot edit poll title after voting has started"
            )

        if new_title == '':
            # TODO: implement poll title update in chat context
            EditPollTitleChatContext(
                user_id=user_id, chat_id=message.chat.id,
                poll_id=poll_id
            ).save_state()

            return await message.reply_text(
                strings.build_poll_title_edit_prompt(poll.desc)
            )
        else:
            prev_title = poll.desc
            poll.desc = new_title
            poll.save()

            return await message.reply_text(
                strings.build_poll_title_edit_message(prev_title, new_title)
            )

    @classmethod
    async def whitelist_username(cls, update: ModifiedTeleUpdate, *_, **__):
        """
        Command usage:
        /whitelist_username {poll_id} {username}
        """
        message = update.message
        message_text = TelegramHelpers.read_raw_command_args(update)
        invalid_format_text = textwrap.dedent(f"""
            Input format is invalid, try:
            /{Command.WHITELIST_USERNAME} {{poll_id}} {{username}}
        """)

        if ' ' not in message_text:
            return await message.reply_text(invalid_format_text)

        raw_poll_id = message_text[:message_text.index(' ')].strip()
        target_username = message_text[message_text.index(' '):].strip()
        if constants.ID_PATTERN.match(raw_poll_id) is None:
            return await message.reply_text('Invalid poll id')

        poll_id = int(raw_poll_id)
        user_id = update.user.get_user_id()
        poll_res = Polls.get_as_creator(poll_id, user_id)
        if poll_res.is_err():
            return await message.reply_text(
                "You're not the creator of this poll"
            )

        poll = poll_res.unwrap()
        whitelist_res = BaseAPI._whitelist_username_for_poll(
            poll=poll, target_username=target_username
        )
        if whitelist_res.is_err():
            err_msg = str(whitelist_res.unwrap_err())
            return await message.reply_text(err_msg)

        return await update.message.reply_text(
            f"Username {target_username} has been whitelisted"
        )

    @admin_only
    async def vote_for_poll_admin(self, update: ModifiedTeleUpdate, *_, **__):
        """
        telegram command formats:
        /vote_admin {@username or #tele_id} {poll_id}: {opt_1} > ... > {opt_n}
        /vote_admin {@username or #tele_id} {poll_id} {opt_1} > ... > {opt_n}
        examples:
        /vote #100 3: 1 > 2 > 3
        /vote #100 3 1 > 2 > 3
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
            if constants.ID_PATTERN.match(raw_tele_id) is None:
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

        return await TelegramHelpers.vote_and_report(
            raw_text, user_tele_id=user_tele_id, message=message,
            username=username, chat_id=message.chat_id
        )

    async def vote_for_poll_handler(
        self, update: ModifiedTeleUpdate, context: CallbackContext
    ):
        """
        telegram command formats
        /vote {poll_id}: {option_1} > {option_2} > ... > {option_n}
        /vote {poll_id} {option_1} > {option_2} > ... > {option_n}
        example:
        /vote 3: 1 > 2 > 3
        /vote 3 1 > 2 > 3
        """
        message = update.message
        user_entry: Users = update.user
        raw_command_args = TelegramHelpers.read_raw_command_args(update)
        tele_user: TeleUser = message.from_user
        assert tele_user is not None

        if raw_command_args == '':
            VoteChatContext(
                user_id=user_entry.get_user_id(), chat_id=message.chat.id,
                max_options=POLL_MAX_OPTIONS
            ).save_state()
            return await message.reply_text(
                "Enter the poll ID of the poll you want to vote for:"
            )
        elif constants.ID_PATTERN.match(raw_command_args) is not None:
            poll_id = int(raw_command_args)
            view_poll_result = self.get_poll_message(
                poll_id=poll_id, user_id=user_entry.get_user_id(),
                bot_username=context.bot.username,
                username=tele_user.username
            )
            if view_poll_result.is_err():
                error_message = view_poll_result.err()
                return await error_message.call(message.reply_text)

            poll_message = view_poll_result.unwrap()
            max_options = poll_message.poll_info.max_options
            vote_context = VoteChatContext(
                user_id=user_entry.get_user_id(), chat_id=message.chat.id,
                max_options=max_options, poll_id=poll_id
            )

            vote_context.save_state()
            return await message.reply_text(
                vote_context.generate_vote_option_prompt()
            )

        raw_text = message.text.strip()
        tele_user: TeleUser | None = update.message.from_user
        username = tele_user.username
        user_tele_id = tele_user.id

        return await TelegramHelpers.vote_and_report(
            raw_text, user_tele_id, message, username=username,
            chat_id=message.chat_id
        )

    @classmethod
    async def delete_poll(cls, update: ModifiedTeleUpdate, *_, **__):
        message: Message = update.message
        tele_user = message.from_user
        user_tele_id = tele_user.id
        user_id = update.user.get_user_id()

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
        delete_comment = f"Poll #{poll_id} user#{user_id} tele#{user_tele_id}"

        if poll is None:
            await message.reply_text(f'poll #{poll_id} not found')
            return False
        elif force_delete:
            logger.warning(f"Deleting {delete_comment}")
            Polls.delete().where(poll_query).execute()
            logger.warning(f"Deleted {delete_comment}")
            await message.reply_text(f'Poll #{poll_id} ({poll.desc}) deleted')
            return True
        elif not poll.closed:
            await message.reply_text(f'poll #{poll_id} must be closed first')
            return False

        markup_layout = [[BaseAPI.spawn_inline_keyboard_button(
            text=f'Delete poll #{poll_id}',
            command=CallbackCommands.DELETE_POLL,
            callback_data=dict(
                poll_id=poll_id, stamp=int(time.time())
            )
        )]]

        reply_markup = InlineKeyboardMarkup(markup_layout)
        await message.reply_text(
            f'Confirm poll #{poll_id} deletion',
            reply_markup=reply_markup
        )
        return True

    @track_errors
    async def delete_account(self, update: ModifiedTeleUpdate, *_, **__):
        tele_user_id = update.message.from_user.id
        assert isinstance(tele_user_id, int)
        deletion_token = TelegramHelpers.read_raw_command_args(update)
        # print('DEL_TOKEN', [deletion_token])

        if deletion_token == '':
            # deletion token not provided, send deletion instructions
            delete_token = self.generate_delete_token(update.user)
            return await update.message.reply_text(
                strings.generate_delete_text(delete_token)
            )

        match_pattern = f'^[0-9A-F]+:[0-9A-F]+$'
        if re.match(match_pattern, deletion_token) is None:
            return await update.message.reply_text('Invalid deletion token')

        user: Users = update.user
        user_id = user.get_user_id()
        assert isinstance(user_id, int)
        hex_stamp, short_hash = deletion_token.split(':')
        deletion_stamp = int(hex_stamp, 16)
        validation_result = self.validate_delete_token(
            user=user, stamp=deletion_stamp, short_hash=short_hash
        )
        if validation_result.is_err():
            err_message = validation_result.err()
            return await update.message.reply_text(err_message)

        logger.warning(
            f"Scheduling deleting user#{user_id} tele#{tele_user_id}"
        )
        try:
            with db.atomic():
                user.deleted_at = datetime.now()  # mark as deleted
                user.save()
        except Exception as e:
            await update.message.reply_text(
                'Unexpected error occurred during account deletion'
            )
            raise e

        logger.warning(
            f"Scheduled deleting user#{user_id} tele#{tele_user_id}"
        )
        return await update.message.reply_text(
            'Account deleted successfully'
        )

    @staticmethod
    async def show_help(update: ModifiedTeleUpdate, *_, **__):
        message: Message = update.message
        await message.reply_text(strings.HELP_TEXT)

    async def view_poll_voters(self, update, *_, **__):
        """
        /view_voters {poll_id}
        :param update:
        :param _:
        :param __:
        :return:
        """
        message: Message = update.message
        extract_result = TelegramHelpers.extract_poll_id(update)

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

        if vote_count >= constants.MAX_DISPLAY_VOTE_COUNT:
            await message.reply_text(
                f'Can only display voters when vote count is '
                f'under {constants.MAX_DISPLAY_VOTE_COUNT}'
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

        return await message.reply_text(textwrap.dedent(f"""
            voted:
            {' '.join(voted_usernames)}
            not voted:
            {' '.join(not_voted_usernames)}
        """))

    @staticmethod
    async def show_about(update: ModifiedTeleUpdate, *_, **__):
        message: Message = update.message
        await message.reply_text(textwrap.dedent(f"""
            Version {strings.__VERSION__}
            The source code for this bot can be found at:
            https://github.com/milselarch/RCV-tele-bot
            Join the feedback and discussion group at: 
            https://t.me/+fs0WPn1pfmYxNjg1
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
        extract_result = TelegramHelpers.extract_poll_id(update)

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

        get_winner_result = self.rcv_tally.get_poll_winner(poll_id)
        winning_option_id, get_status = get_winner_result

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

    @classmethod
    async def payment_support_handler(
        cls, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        user = update.user
        message = update.message
        PaySupportChatContext(
            user_id=user.get_user_id(), chat_id=message.chat.id
        ).save_state()

        return await message.reply_text(textwrap.dedent("""
            Please enter the following details:
            - payment reference ID
            - reason for creating the payment support ticket
        """))

    @admin_only
    async def refund_payment_support_handler(
        self, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        """
        /refund_admin {tele_user_id}| {payment_ref_id}
        """
        message = update.message
        r"""
        ^\S+ - command name
        \s+ - whitespace
        ([1-9]\d*) - target user ID
        \s+ - whitespace
        (\S+) - payment reference ID 
        """
        pattern = re.compile(r'^\S+\s+([1-9]\d*)\s+(\S+)$')
        matches = pattern.match(message.text)

        if matches is None:
            await message.reply_text(f'Format invalid (1)')
            return False

        capture_groups = matches.groups()
        if len(capture_groups) != 2:
            await message.reply_text(f'Format invalid (2)')
            return False

        target_tele_user_id = int(capture_groups[0])
        payment_ref_id = capture_groups[1]
        bot = self.get_bot()
        payment_res = Payments.build_from_fields(
            telegram_payment_charge_id=payment_ref_id
        ).safe_get()

        if payment_res.is_err():
            await message.reply_text(f'Payment db entry not found')
        else:
            payment = payment_res.unwrap()
            if payment.refunded_at is not None:
                await message.reply_text(f'Payment has already been refunded')
                return False

        await bot.refund_star_payment(
            user_id=target_tele_user_id,
            telegram_payment_charge_id=payment_ref_id
        )
        await message.reply_text(f'Payment refunded successfully')
        if payment_res.is_ok():
            payment = payment_res.unwrap()
            payment.refunded_at = datetime.now()
            payment.save()

        return None

    @admin_only
    async def enter_maintenance_admin(
        self, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        message = update.message
        self.payment_handlers.enter_maintenance_mode()
        return await message.reply_text('Maintenance mode entered')

    @admin_only
    async def exit_maintenance_admin(
        self, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        message = update.message
        self.payment_handlers.exit_maintenance_mode()
        return await message.reply_text('Maintenance mode exited')

    @admin_only
    async def send_msg_admin(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        raw_args = TelegramHelpers.read_raw_command_args(update)
        split_index = raw_args.index(' ')
        chat_id = int(raw_args[:split_index])
        payload = raw_args[split_index+1:]
        reply_text = update.message.reply_text

        try:
            response = await context.bot.send_message(
                chat_id=chat_id, text=payload
            )
        except telegram.error.BadRequest:
            return await reply_text("Failed to send message")

        logger.info(f"SEND_RESP {response}")
        return await reply_text("Message sent")


if __name__ == '__main__':
    rcv_bot = RankedChoiceBot()
    rcv_bot.start_bot()
