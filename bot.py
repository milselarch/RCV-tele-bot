import json
import logging
import time

import telegram
import traceback
import textwrap
import re

from load_config import *
from BaseAPI import BaseAPI, UserRegistrationStatus
from json import JSONDecodeError
from result import Ok, Err, Result
from MessageBuilder import MessageBuilder
from requests.models import PreparedRequest
from RankedChoice import SpecialVotes
from typing import List, Tuple, Dict, Optional, Sequence
from database import (
    Users, Polls, PollVoters, UsernameWhitelist,
    PollOptions, VoteRankings, db, ChatWhitelist
)

from telegram import (
    Update, Message, WebAppInfo, ReplyKeyboardMarkup,
    KeyboardButton, User, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    CommandHandler, ApplicationBuilder, ContextTypes,
    MessageHandler, filters, CallbackContext, CallbackQueryHandler
)


ID_PATTERN = re.compile(r"^[1-9]\d*$")
REGISTER_CALLBACK_CMD = "REGISTER"
MAX_DISPLAY_VOTE_COUNT = 30

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

"""
to do:
/ create poll
/ view poll options / votes
/ vote on a poll        
# TODO: only allow poll results to be seen if everyone voted

/ fetch poll results 
automatically calculate + broadcast poll results
"""


def error_logger(update, context):
    """Log Errors caused by Updates."""
    logger.warning(
        'Update "%s" caused error "%s"',
        update, context.error
    )


def track_errors(func):
    def caller(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(traceback.format_exc())
            raise e

    return caller


class RankedChoiceBot(BaseAPI):
    def __init__(self, config_path='config.yml'):
        super().__init__()
        self.config_path = config_path
        self.bot = None
        self.app = None

        self.poll_max_options = 20
        self.poll_option_max_length = 100
        self.webhook_url = None

    @staticmethod
    def record_username_wrapper(func):
        """
        updates user id to username mapping
        """
        def caller(update: Update, *args, **kwargs):
            # print("UPDATE", update)
            message: Message = update.message
            user: User = message.from_user
            chat_username: str = user.username

            Users.insert(id=user.id, username=chat_username).on_conflict(
                preserve=[Users.id],
                update={Users.username: chat_username}
            ).execute()

            return func(update, *args, **kwargs)

        return caller

    @classmethod
    def wrap_command_handler(cls, handler):
        return track_errors(cls.record_username_wrapper(handler))

    def start_bot(self):
        self.bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        self.webhook_url = TELE_CONFIG['webhook_url']
        self.app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

        commands_mapping = self.kwargify(
            start=self.start_handler,
            user_details=self.user_details_handler,
            create_poll=self.create_poll,
            create_group_poll=self.create_group_poll,
            whitelist_chat_registration=self.whitelist_chat_registration,
            blacklist_chat_registration=self.blacklist_chat_registration,

            view_poll=self.view_poll,
            vote=self.vote_for_poll,
            poll_results=self.fetch_poll_results,
            has_voted=self.has_voted,
            close_poll=self.close_poll,
            view_votes=self.view_votes,
            view_voters=self.view_poll_voters,
            about=self.show_about,
            help=self.show_help,

            vote_admin=self.vote_for_poll_admin,
            unclose_poll_admin=self.unclose_poll_admin,
            close_poll_admin=self.close_poll_admin
        )

        # on different commands - answer in Telegram
        self.register_commands(
            self.app, commands_mapping=commands_mapping,
            wrap_func=self.wrap_command_handler
        )
        # catch-all to handle responses to unknown commands
        self.app.add_handler(MessageHandler(
            filters.Regex(r'^/') & filters.COMMAND,
            self.handle_unknown_command
        ))
        self.app.add_handler(MessageHandler(
            filters.StatusUpdate.WEB_APP_DATA, self.web_app_handler
        ))
        self.app.add_handler(CallbackQueryHandler(
            self.inline_keyboard_handler
        ))

        self.app.run_polling(allowed_updates=Update.ALL_TYPES)

    async def start_handler(
        self, update, context: ContextTypes.DEFAULT_TYPE
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
        user: User = update.message.from_user
        user_id = user.id

        view_poll_result = self._view_poll(
            poll_id=poll_id, user_id=user_id,
            bot_username=context.bot.username,
            username=user.username
        )

        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_message = view_poll_result.unwrap()
        reply_markup = ReplyKeyboardMarkup(self.build_private_vote_markup(
            poll_id=poll_id, user=user
        ))

        await message.reply_text(poll_message, reply_markup=reply_markup)

    @track_errors
    @record_username_wrapper
    async def web_app_handler(self, update: Update, _):
        payload = json.loads(update.effective_message.web_app_data.data)
        poll_id = int(payload['poll_id'])
        ranked_option_numbers: List[int] = payload['option_numbers']

        message: Message = update.message
        user: User = message.from_user
        username: Optional[str] = user.username
        user_id = user.id

        formatted_rankings = ' > '.join([
            self.stringify_ranking(rank) for rank in ranked_option_numbers
        ])
        await message.reply_text(textwrap.dedent(f"""
            Your rankings are:
            {poll_id}: {formatted_rankings}
        """))

        vote_result = self.register_vote(
            poll_id=poll_id, rankings=ranked_option_numbers,
            user_id=user_id, username=username
        )

        if vote_result.is_err():
            error_message = vote_result.err()
            await error_message.call(message.reply_text)
            return False

        await message.reply_text(textwrap.dedent(f"""
            vote has been registered
        """))

    @track_errors
    @record_username_wrapper
    async def handle_unknown_command(self, update: Update, _):
        await update.message.reply_text("Command not found")

    def generate_poll_url(self, poll_id: int, user: User) -> str:
        req = PreparedRequest()
        auth_date = str(int(time.time()))
        query_id = self.generate_secret()
        user_info = json.dumps({
            'id': user.id,
            'username': user.username
        })

        data_check_string = self.make_data_check_string(
            auth_date=auth_date, query_id=query_id, user=user_info
        )
        validation_hash = self.sign_data_check_string(
            data_check_string=data_check_string, bot_token=TELEGRAM_BOT_TOKEN
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
        self, poll_id: int, user: User
    ) -> List[List[KeyboardButton]]:
        poll_url = self.generate_poll_url(poll_id=poll_id, user=user)
        logger.info(f'POLL_URL = {poll_url}')
        # create vote button for reply message
        markup_layout = [[KeyboardButton(
            text=f'Vote for Poll #{poll_id}',
            web_app=WebAppInfo(url=poll_url)
        )]]

        return markup_layout

    def build_group_vote_markup(
        self, poll_id: int
    ) -> List[List[InlineKeyboardButton]]:
        callback_data = json.dumps(self.kwargify(
            poll_id=poll_id, command=REGISTER_CALLBACK_CMD
        ))
        markup_layout = [[InlineKeyboardButton(
            text=f'Register for poll', callback_data=callback_data
        )]]
        return markup_layout

    @track_errors
    async def inline_keyboard_handler(
        self, update: Update, _: CallbackContext
    ):
        """
        callback method for buttons in chat group messages
        """
        query = update.callback_query
        chat_id = query.message.chat_id
        raw_callback_data = query.data
        user = query.from_user

        if user is None:
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
        elif callback_data['command'] == REGISTER_CALLBACK_CMD:
            poll_id = callback_data['poll_id']
            if not self.is_whitelisted_chat(poll_id=poll_id, chat_id=chat_id):
                await query.answer("Not allowed to register from this chat")
                return False

            registration_status = self._register_voter(
                poll_id=poll_id, user_id=user.id, username=user.username
            )

            match registration_status:
                case UserRegistrationStatus.REGISTERED:
                    await query.answer("Registered for poll")
                    return True
                case UserRegistrationStatus.ALREADY_REGISTERED:
                    await query.answer("Already registered for poll")
                    return False
                case UserRegistrationStatus.FAILED:
                    await query.answer("Registration failed")
                    return False
        else:
            await query.answer("unknown callback command")
            return False

    @staticmethod
    def is_whitelisted_chat(poll_id: int, chat_id: int):
        query = ChatWhitelist.select().where(
            (ChatWhitelist.chat_id == chat_id) &
            (ChatWhitelist.poll_id == poll_id)
        )
        return query.exists()

    @staticmethod
    async def user_details_handler(update: Update, *_):
        """
        returns current user id and username
        """
        # when command /user_details is invoked
        user = update.message.from_user
        await update.message.reply_text(textwrap.dedent(f"""
            user id: {user.id}
            username: {user.username}
        """))

    async def has_voted(self, update: Update, *_, **__):
        """
        usage:
        /has_voted {poll_id}
        """
        message = update.message
        user = update.message.from_user
        user_id = user.id

        extract_poll_id_result = self.extract_poll_id(update)
        if extract_poll_id_result.is_err():
            return False

        poll_id = extract_poll_id_result.unwrap()
        is_voter = self.is_poll_voter(
            poll_id=poll_id, user_id=user_id
        )

        if not is_voter:
            await message.reply_text(
                f"You're not a voter of poll {poll_id}"
            )
            return False

        has_voted = self.check_has_voted(
            poll_id=poll_id, user_id=user_id
        )

        if has_voted:
            await message.reply_text("you've voted already")
        else:
            await message.reply_text("you haven't voted")

    async def create_group_poll(
        self, update, context: ContextTypes.DEFAULT_TYPE
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
            print('CHAT_ID', chat_id)
            whitelisted_chat_ids.append(chat_id)

        return await self.create_poll(
            update=update, context=context, open_registration=True,
            whitelisted_chat_ids=whitelisted_chat_ids
        )

    async def create_poll(
        self, update, context: ContextTypes.DEFAULT_TYPE,
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
        message = update.message
        creator_user = message.from_user
        creator_user_id = creator_user.id
        raw_text = message.text.strip()
        # print('CHAT_IDS', whitelisted_chat_ids)
        # TODO: check if usernames passed is within poll limit

        if ':' not in raw_text:
            await message.reply_text("poll creation format wrong")
            return False

        split_index = raw_text.index(':')
        # first part of command is all the users that are in the poll
        command_p1: str = raw_text[:split_index].strip()
        # second part of command is the poll question + poll options
        command_p2: str = raw_text[split_index + 1:].strip()

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
        else:
            await message.reply_text('poll voters not specified!')
            return False

        raw_poll_usernames: List[str] = command_p1.split()
        poll_usernames: List[str] = []
        poll_user_ids: List[int] = []

        for raw_poll_user in raw_poll_usernames:
            if raw_poll_user.startswith('#'):
                if ID_PATTERN.match(raw_poll_user) is None:
                    await message.reply_text(
                        f'Invalid poll user id: {raw_poll_user}'
                    )
                    return False

                poll_user_id = int(raw_poll_user[1:])
                poll_user_ids.append(poll_user_id)
                continue

            if raw_poll_user.startswith('@'):
                poll_username = raw_poll_user[1:]
            else:
                poll_username = raw_poll_user

            if len(poll_username) < 4:
                await message.reply_text(
                    f'username too short: {poll_username}'
                )
                return False

            poll_usernames.append(poll_username)

        with db.atomic():
            new_poll = Polls.create(
                desc=poll_question, creator_id=creator_user_id,
                num_voters=len(poll_user_ids),
                open_registration=open_registration
            )

            new_poll_id: int = new_poll.id
            assert isinstance(new_poll_id, int)
            chat_whitelist_rows = []
            whitelisted_user_rows = []
            poll_option_rows = []
            poll_voter_rows = []

            # create poll options
            for k, poll_option in enumerate(poll_options):
                poll_choice_number = k+1
                poll_option_rows.append(self.kwargify(
                    poll_id=new_poll_id, option_name=poll_option,
                    option_number=poll_choice_number
                ))
            # whitelist voters in poll by username
            for raw_poll_user in poll_usernames:
                whitelisted_user_rows.append(self.kwargify(
                    poll_id=new_poll_id, username=raw_poll_user
                ))
            # whitelist voters in poll by user id
            for poll_user_id in poll_user_ids:
                poll_voter_rows.append(self.kwargify(
                    poll_id=new_poll_id, user_id=poll_user_id
                ))

            # chat ids that are whitelisted for user self-registration
            for chat_id in whitelisted_chat_ids:
                chat_whitelist_rows.append(self.kwargify(
                    poll_id=new_poll_id, chat_id=chat_id
                ))

            PollVoters.insert_many(poll_voter_rows).execute()
            UsernameWhitelist.insert_many(whitelisted_user_rows).execute()
            PollOptions.insert_many(poll_option_rows).execute()
            ChatWhitelist.insert_many(chat_whitelist_rows).execute()
            new_poll.num_voters = len(whitelisted_user_rows)
            new_poll.save()

        bot_username = context.bot.username
        poll_message = self.generate_poll_info(
            new_poll_id, poll_question, poll_options,
            bot_username=bot_username,
            num_voters=len(poll_usernames)
        )

        user: User = update.message.from_user
        chat_type = update.message.chat.type
        reply_markup = None

        if chat_type == 'private':
            # create vote button for reply message
            vote_markup_data = self.build_private_vote_markup(
                poll_id=new_poll_id, user=user
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

    async def whitelist_chat_registration(
        self, update: Update, *_, **__
    ):
        return await self.set_chat_registration_status(
            update=update, whitelist=True
        )

    async def blacklist_chat_registration(
        self, update: Update, *_, **__
    ):
        return await self.set_chat_registration_status(
            update=update, whitelist=False
        )

    async def set_chat_registration_status(
        self, update: Update, whitelist: bool
    ) -> bool:
        message = update.message
        user = message.from_user

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

        if poll.creator_id != user.id:
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
        else:
            # noinspection PyUnresolvedReferences
            try:
                whitelist_row = ChatWhitelist.get(
                    (ChatWhitelist.poll_id == poll_id) &
                    (ChatWhitelist.chat_id == message.chat.id)
                )
            except ChatWhitelist.DoesNotExist:
                await message.reply_text(
                    f'Chat was not whitelisted for user self-registration '
                    f'to begin with'
                )
                return False

            whitelist_row.delete_instance().execute()
            await message.reply_text(
                f'Removed user self-registration chat whitelist'
            )

        return True

    async def view_votes(self, update: Update, *_, **__):
        message: Message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()
        user: User = update.message.from_user
        user_id = user.id

        # check if voter is part of the poll
        get_poll_closed_result = self.get_poll_closed(poll_id)
        if get_poll_closed_result.is_err():
            error_message = get_poll_closed_result.err()
            await error_message.call(message.reply_text)

        has_poll_access = self.has_poll_access(
            poll_id, user_id, username=user.username
        )
        if not has_poll_access:
            await message.reply_text(
                f'You have no access to poll {poll_id}'
            )
            return False

        # get poll options in ascending order
        poll_option_rows = PollOptions.select().where(
            PollOptions.poll_id == poll_id
        ).order_by(PollOptions.option_number)

        # map poll option ids to their option ranking numbers
        # (option number is the position of the option in the poll)
        option_index_map = {}
        for poll_option_row in poll_option_rows:
            option_index_map[poll_option_row.id] = (
                poll_option_row.option_number
            )

        vote_rows = VoteRankings.select().join(
            PollVoters, on=(PollVoters.id == VoteRankings.poll_voter_id)
        ).where(
            PollVoters.poll_id == poll_id
        ).order_by(
            VoteRankings.option_id, VoteRankings.ranking
        )

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
            voter_id: int = vote_row.poll_voter_id.id
            assert isinstance(voter_id, int)

            if voter_id not in vote_sequence_map:
                vote_sequence_map[voter_id] = {}

            option_id_row = vote_row.option_id

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

    async def _set_poll_status(self, update, closed=True):
        message = update.message
        user = update.message.from_user
        user_id = user['id']

        if user_id != YAML_CONFIG['telegram']['sudo_id']:
            await message.reply_text('ACCESS DENIED')
            return False

        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()
        cache_key = self._build_poll_winner_cache_key(poll_id)

        with db.atomic():
            # remove cached result for poll winner
            self.redis_cache.delete(cache_key)
            Polls.update({Polls.closed: closed}).where(
                Polls.id == poll_id
            ).execute()

        await message.reply_text(f'poll {poll_id} has been unclosed')

    async def view_poll(self, update, context: ContextTypes.DEFAULT_TYPE):
        """
        example:
        /view_poll 3
        """
        message = update.message
        user = update.message.from_user
        extract_result = self.extract_poll_id(update)
        user_id = user.id

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.unwrap()
        view_poll_result = self._view_poll(
            poll_id=poll_id, user_id=user_id,
            bot_username=context.bot.username,
            username=user.username
        )

        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            await error_message.call(message.reply_text)
            return False

        chat_type = update.message.chat.type
        reply_markup = None

        if chat_type == 'private':
            # create vote button for reply message
            vote_markup_data = self.build_private_vote_markup(
                poll_id=poll_id, user=user
            )
            reply_markup = ReplyKeyboardMarkup(vote_markup_data)

        poll_message = view_poll_result.unwrap()
        await message.reply_text(poll_message, reply_markup=reply_markup)
        return True

    async def vote_and_report(
        self, raw_text: str, user_id: int, message: Message,
        username: Optional[str]
    ):
        vote_result = self._vote_for_poll(
            raw_text=raw_text, user_id=user_id,
            username=username
        )

        if vote_result.is_err():
            error_message = vote_result.err()
            await error_message.call(message.reply_text)
            return False

        # poll_id = vote_result.unwrap()
        await message.reply_text(textwrap.dedent(f"""
            vote has been registered
        """))

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
        user = message.from_user

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            await message.reply_text(f'poll {poll_id} does not exist')
            return False

        creator_id = poll.creator_id.id
        assert isinstance(creator_id, int)

        if creator_id != user.id:
            await message.reply_text(
                'only poll creator is allowed to close poll'
            )
            return False

        poll.closed = True
        poll.save()

        winning_option_id = await self.get_poll_winner(poll_id)
        if winning_option_id is not None:
            winning_options = PollOptions.select().where(
                PollOptions.id == winning_option_id
            )

            option_name = winning_options[0].option_name
            await message.reply_text(textwrap.dedent(f"""
                Poll closed
                Poll winner is:
                {option_name}
            """))
        else:
            await message.reply_text(textwrap.dedent(f"""
                Poll closed
                Poll has no winner
            """))

    async def vote_for_poll_admin(self, update: Update, *_, **__):
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
        user = update.message.from_user
        user_id = user.id

        if user_id != YAML_CONFIG['telegram']['sudo_id']:
            await message.reply_text('ACCESS DENIED')
            return False

        if ' ' not in raw_text:
            await message.reply_text('no user specified')
            return False

        raw_text = raw_text[raw_text.index(' ')+1:].strip()
        if ' ' not in raw_text:
            await message.reply_text('no poll_id specified (admin)')
            return False

        username_or_id = raw_text[:raw_text.index(' ')].strip()
        # raw_text = raw_text[raw_text.index(' ')+1:].strip()
        # print('RAW', [raw_text])

        if username_or_id.startswith('@'):
            # resolve telegram user_id by username
            username_or_id = username_or_id[1:]
            matching_users = Users.select().where(
                Users.username == username_or_id
            )

            if len(matching_users) > 1:
                user_ids = [user.id for user in matching_users]
                await message.reply_text(
                    f'multiple users with same username: {user_ids}'
                )
                return False
            elif len(matching_users) == 0:
                await message.reply_text('No matching users found')
                return False

            user_id = matching_users[0].id
        elif username_or_id.startswith('#'):
            # try to parse user_id directly
            raw_user_id = username_or_id[1:].strip()
            if ID_PATTERN.match(raw_user_id) is None:
                await message.reply_text(f'invalid user id: {raw_user_id}')
                return False

            user_id = int(raw_user_id)
        else:
            await message.reply_text(f'@username or #user_id required')
            return False

        if ' ' not in raw_text:
            await message.reply_text('invalid format (admin)')
            return False

        print('CHAT_USERNAME', username_or_id)
        # raw_text = raw_text[raw_text.index(' ')+1:].strip()
        # print('RAW', [raw_text])

        await self.vote_and_report(
            raw_text, user_id=user_id, message=message,
            username=user.username
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
        user = update.message.from_user
        username = user.username
        user_id = user.id

        await self.vote_and_report(
            raw_text, user_id, message, username=username
        )

    def _vote_for_poll(
        self, raw_text: str, user_id: int, username: Optional[str]
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
        print('RAW_VOTE_TEXT', [raw_text, user_id])
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

        return self.register_vote(
            poll_id=poll_id, rankings=rankings,
            user_id=user_id, username=username
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
        cls, raw_text
    ) -> Result[Tuple[int, List[int]], MessageBuilder]:
        """
        raw_text format:
        {command} {poll_id}: {choice_1} > {choice_2} > ... > {choice_n}
        """
        error_message = MessageBuilder()
        # remove starting command from raw_text
        raw_arguments = raw_text[raw_text.index(' '):].strip()

        """
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
        print('RAW', raw_arguments)
        pattern_match1 = re.match(
            '^[0-9]+:?\s+(\s*[1-9]+0*\s*>)*\s*([0-9]+|{}|{})$'.format(
                *SpecialVotes.get_str_values()
            ), raw_arguments
        )
        """
        catches input of format:
        {poll_id} {choice_1} {choice_2} ... {choice_n}

        regex breakdown:
        ^ -> start of string
        ([0-9]+):*\s* -> poll_id, optional colon
        ([1-9]+0*\s+)* -> ranking number (>0) then space
        ([0-9]+|withhold|abstain) -> final ranking number or special vote
        $ -> end of string        
        """
        pattern_match2 = re.match(
            '^([0-9]+):?\s*([1-9]+0*\s+)*([0-9]+|{}|{})$'.format(
                *SpecialVotes.get_str_values()
            ), raw_arguments
        )

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
            raw_arguments = re.sub('\s+', ' ', raw_arguments)
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
    async def show_about(update: Update, *_, **__):
        message: Message = update.message
        await message.reply_text(textwrap.dedent("""
            The source code for this bot can be found at:
            https://github.com/milselarch/RCV-tele-bot
        """))

    @staticmethod
    async def show_help(update: Update, *_, **__):
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
        - creates a new poll
        ——————————————————
        /create_group_poll @username_1 @username_2 ... @username_n:
        poll title
        poll option 1
        poll option 2
        ...
        poll option m
        - creates a new poll that chat members can self-register for
        ——————————————————
        /whitelist_chat_registration {poll_id}
        whitelists the current chat so that chat members can self-register
        for the poll specified by poll_id within the chat group
        ——————————————————
        /blacklist_chat_registration {poll_id}
        blacklists the current chat so that chat members cannot self-register
        for the poll specified by poll_id within the chat group
        ——————————————————
        /view_poll {poll_id} - shows poll details given poll_id
        ——————————————————
        /vote {poll_id}: {option_1} > {option_2} > ... > {option_n} 
        /vote {poll_id} {option_1} > {option_2} > ... > {option_n} 
        /vote {poll_id} {option_1} {option_2} ... {option_n} 

        Last option can also accept 2 special values, withhold and abstain:
            > Vote withhold if you want to vote for none of the options
            > Vote abstain if you want to remove yourself from the poll 

        - vote for the poll with the specified poll_id
        requires that the user is one of the registered 
        voters of the poll
        ——————————————————
        /poll_results {poll_id}
        - returns poll results if the poll has been closed
        ——————————————————
        /has_voted {poll_id} 
        - tells you if you've voted for the poll with the 
        specified poll_id
        ——————————————————
        /close_poll {poll_id}
        - close the poll with the specified poll_id
        note that only the poll's creator is allowed 
        to issue this command to close the poll
        ——————————————————
        /view_votes {poll_id}
        - view all the votes entered for the poll 
        with the specified poll_id. This can only be done
        after the poll has been closed first
        ——————————————————
        /view_voters {poll_id}
        - show which voters have voted and which have not
        ——————————————————
        /about - view miscellaneous information about the bot
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
        user: User = message.from_user
        user_id = user.id
        # check if voter is part of the poll

        has_poll_access = self.has_poll_access(
            poll_id, user_id, username=user.username
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
        poll_voters = PollVoters.select().join(
            Users, on=(PollVoters.user_id == Users.id)
        ).where(
            PollVoters.poll_id == poll_id
        )

        if vote_count >= MAX_DISPLAY_VOTE_COUNT:
            await message.reply_text(
                f'Can only display voters when vote count is '
                f'under {MAX_DISPLAY_VOTE_COUNT}'
            )
            return False

        voted_usernames, not_voted_usernames = [], []
        recorded_user_ids = set()

        for voter in poll_voters:
            # print('VOTER', voter.dicts())
            username: str = voter.username
            user_id: int = voter.user_id.id
            recorded_user_ids.add(user_id)

            if voter.voted:
                voted_usernames.append(username)
            else:
                not_voted_usernames.append(username)

        whitelisted_usernames = UsernameWhitelist.select().where(
            UsernameWhitelist.poll_id == poll_id
        )

        for whitelist_entry in whitelisted_usernames:
            optional_user_id = whitelist_entry.user_id
            username: str = whitelist_entry.username

            if optional_user_id is not None:
                user_id: int = whitelist_entry.user_id.id
                if user_id not in recorded_user_ids:
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
        user = message.from_user
        user_id = user.id

        # check if voter is part of the poll
        has_poll_access = self.has_poll_access(
            poll_id, user_id, username=user.username
        )
        if not has_poll_access:
            await message.reply_text(f'You have no access to poll {poll_id}')
            return False

        get_poll_closed_result = self.get_poll_closed(poll_id)
        if get_poll_closed_result.is_err():
            error_message = get_poll_closed_result.err()
            await error_message.call(message.reply_text)

        winning_option_id = await self.get_poll_winner(poll_id)

        if winning_option_id is None:
            await message.reply_text('no poll winner')
            return False
        else:
            winning_options = PollOptions.select().where(
                PollOptions.id == winning_option_id
            )

            option_name = winning_options[0].option_name
            await message.reply_text(f'poll winner is:\n{option_name}')

    @staticmethod
    def register_commands(
        dispatcher, commands_mapping, wrap_func=lambda func: func
    ):
        for command_name in commands_mapping:
            handler = commands_mapping[command_name]
            wrapped_handler = wrap_func(handler)
            dispatcher.add_handler(CommandHandler(
                command_name, wrapped_handler
            ))


if __name__ == '__main__':
    rcv_bot = RankedChoiceBot()
    rcv_bot.start_bot()
