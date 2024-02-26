import json
import logging
import time

import telegram
import traceback
import textwrap
import re

import RankedChoice

from database import *
from load_config import *
from BaseAPI import BaseAPI
from result import Ok, Err, Result
from RankedVote import RankedVote
from MessageBuilder import MessageBuilder
from requests.models import PreparedRequest
from RankedChoice import SpecialVotes
from typing import List, Tuple, Dict

from telegram import (
    Update, Message, WebAppInfo, ReplyKeyboardMarkup,
    KeyboardButton, User
)
from telegram.ext import (
    CommandHandler, ApplicationBuilder, ContextTypes,
    MessageHandler, filters
)

# Enable logging
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
        self.config_path = config_path
        self.bot = None
        self.app = None

        self.poll_max_options = 20
        self.poll_option_max_length = 100
        self.webhook_url = None

    def start_bot(self):
        self.bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        self.webhook_url = TELE_CONFIG['webhook_url']
        self.app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

        # on different commands - answer in Telegram
        self.register_commands(self.app, commands_mapping=self.kwargify(
            start=self.start_handler,
            user_details=self.name_id_handler,
            create_poll=self.create_poll,
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
        ))

        # catch-all to handle responses to unknown commands
        self.app.add_handler(MessageHandler(
            filters.Regex(r'^/') & filters.COMMAND,
            self.handle_unknown_command
        ))
        self.app.add_handler(MessageHandler(
            filters.StatusUpdate.WEB_APP_DATA, self.web_app_data
        ))

        self.app.run_polling(allowed_updates=Update.ALL_TYPES)

    @track_errors
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
        chat_username: str = user.username

        view_poll_result = self._view_poll(
            poll_id=poll_id, chat_username=chat_username,
            bot_username=context.bot.username
        )

        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_message = view_poll_result.ok()
        reply_markup = ReplyKeyboardMarkup(self.build_vote_markup(
            poll_id=poll_id, user=user
        ))

        await message.reply_text(poll_message, reply_markup=reply_markup)

    @track_errors
    async def web_app_data(self, update: Update, _):
        payload = json.loads(update.effective_message.web_app_data.data)
        poll_id = int(payload['poll_id'])
        ranked_option_numbers: List[int] = payload['option_numbers']

        message: Message = update.message
        user: User = message.from_user
        chat_username: str = user.username

        formatted_rankings = ' > '.join([
            str(rank) for rank in ranked_option_numbers
        ])
        await message.reply_text(textwrap.dedent(f"""
            Your rankings are:
            {poll_id}: {formatted_rankings}
        """))

        vote_result = self.register_vote(
            poll_id=poll_id, rankings=ranked_option_numbers,
            chat_username=chat_username
        )

        if vote_result.is_err():
            error_message = vote_result.err()
            await error_message.call(message.reply_text)
            return False

        await self.do_post_vote_actions(poll_id=poll_id, message=message)

    @track_errors
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

    def build_vote_markup(
        self, poll_id: int, user: User
    ) -> List[List[KeyboardButton]]:
        poll_url = self.generate_poll_url(poll_id=poll_id, user=user)
        logger.info(f'POLL_URL = {poll_url}')
        # create vote button for reply message
        markup_layout = [[KeyboardButton(
            text=f'Vote for Poll #{poll_id}', web_app=WebAppInfo(url=poll_url)
        )]]

        return markup_layout

    @track_errors
    async def name_id_handler(self, update, *args):
        """
        returns current user id and username
        """
        # when command /user_details is invoked
        user = update.message.from_user
        await update.message.reply_text(textwrap.dedent(f"""
            user id: {user['id']}
            username: {user['username']}
        """))

    @track_errors
    async def has_voted(self, update, *args, **kwargs):
        """
        usage:
        /has_voted {poll_id}
        """
        message = update.message
        user = update.message.from_user
        chat_username = user['username']

        extract_poll_id_result = self.extract_poll_id(update)
        if extract_poll_id_result.is_err():
            return False

        poll_id: int = extract_poll_id_result.ok()
        is_voter = self.is_poll_voter(
            poll_id=poll_id, chat_username=chat_username
        )

        if not is_voter:
            await message.reply_text(
                f"You're not a voter of poll {poll_id}"
            )
            return False

        has_voted = self.check_has_voted(
            poll_id=poll_id, chat_username=chat_username
        )

        if has_voted:
            await message.reply_text("you've voted already")
        else:
            await message.reply_text("you haven't voted")

    @track_errors
    async def create_poll(self, update, context: ContextTypes.DEFAULT_TYPE):
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
        creator_user = update.message.from_user
        creator_username = creator_user['username']
        message = update.message
        raw_text = message.text.strip()

        if ':' not in raw_text:
            await message.reply_text("poll creation format wrong")
            return False

        split_index = raw_text.index(':')
        # first part of command is all the users that are in the poll
        command_p1 = raw_text[:split_index].strip()
        # second part of command is the poll question + poll options
        command_p2 = raw_text[split_index + 1:].strip()

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

        poll_usernames = command_p1.split()
        poll_users = []

        for poll_user in poll_usernames:
            # TODO: find a way to get all users in a telegram group
            # telegram usernames must be at least 4 characters long
            if poll_user == 'all':
                if message.chat.type != 'group':
                    await message.reply_text(
                        'can only add all users in a group'
                    )
                    return False
                else:
                    await message.reply_text(
                        'adding all users in a group is not suppoerted'
                    )
                    return False
            else:
                if poll_user.startswith('@'):
                    poll_user = poll_user[1:]
                if len(poll_user) < 4:
                    await message.reply_text(
                        f'username too short: {poll_user}'
                    )
                    return False

                poll_users.append(poll_user)

        new_poll = Polls.create(
            desc=poll_question, creator=creator_username
        )

        new_poll.save()
        new_poll_id: int = new_poll.id
        assert isinstance(new_poll_id, int)
        poll_option_rows = []
        poll_user_rows = []

        for k, poll_option in enumerate(poll_options):
            poll_choice_number = k+1
            poll_option_rows.append(self.kwargify(
                poll_id=new_poll_id, option_name=poll_option,
                option_number=poll_choice_number
            ))

        for poll_user in poll_users:
            poll_user_rows.append(self.kwargify(
                poll_id=new_poll_id, username=poll_user
            ))

        group_id = update.message.chat_id
        chat = Chats.create(
            poll_id=new_poll_id, tele_id=group_id,
            broadcasted=False
        )

        with db.atomic():
            Options.insert_many(poll_option_rows).execute()
            PollVoters.insert_many(poll_user_rows).execute()
            chat.save()

        bot_username = context.bot.username
        poll_message = self.generate_poll_info(
            new_poll_id, poll_question, poll_options,
            bot_username=bot_username,
            num_voters=len(poll_users)
        )

        user: User = update.message.from_user
        chat_type = update.message.chat.type
        reply_markup = None

        if chat_type == 'private':
            # create vote button for reply message
            reply_markup = ReplyKeyboardMarkup(self.build_vote_markup(
                poll_id=new_poll_id, user=user
            ))

        await message.reply_text(
            poll_message, reply_markup=reply_markup
        )

    @track_errors
    async def view_votes(self, update: Update, *args, **kwargs):
        message: Message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id: int = extract_result.ok()
        user: User = update.message.from_user
        chat_username: str = user.username
        assert isinstance(chat_username, str)
        # check if voter is part of the poll

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            await message.reply_text(f'poll {poll_id} does not exist')
            return False

        if not poll.closed:
            await message.reply_text(
                'poll votes can only be viewed after closing'
            )
            return False

        has_poll_access = self.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            await message.reply_text(
                f'You have no access to poll {poll_id}'
            )
            return False

        # get poll options in ascending order
        poll_option_rows = Options.select().where(
            Options.poll_id == poll_id
        ).order_by(Options.option_number)

        # map poll option ids to their option ranking numbers
        # (option number is the position of the option in the poll)
        option_index_map = {}
        for poll_option_row in poll_option_rows:
            option_index_map[poll_option_row.id] = (
                poll_option_row.option_number
            )

        vote_rows = (Votes.select()
            .where(Votes.poll_id == poll_id)
            .order_by(Votes.option_id, Votes.ranking)
        )

        vote_sequence_map: Dict[int, Dict[int, int]] = {}
        for vote_row in vote_rows:
            """
            Maps voters to their ranked vote
            Each ranked vote is stored as a dictionary
            mapping their vote ranking to a vote_value
            Each vote_value is either a poll option_id 
            (which is always a positive number), 
            or either of the 0 or nil special votes
            (which are represented as negative numbers -1 and -2)
            """
            voter_id: int = vote_row.poll_voter_id.id
            assert isinstance(voter_id, int)

            if voter_id not in vote_sequence_map:
                vote_sequence_map[voter_id] = {}

            option_id: int = vote_row.option_id.id
            assert isinstance(option_id, int)

            if option_id is None:
                vote_value = vote_row.special_value
                assert vote_value < 0
            else:
                vote_value = option_id
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

    @track_errors
    async def unclose_poll_admin(self, update, *args, **kwargs):
        await self._set_poll_status(update, False)

    @track_errors
    async def close_poll_admin(self, update, *args, **kwargs):
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

        poll_id = extract_result.ok()
        Polls.update({Polls.closed: closed}).where(
            Polls.id == poll_id
        ).execute()

        await message.reply_text(f'poll {poll_id} has been unclosed')

    @track_errors
    async def view_poll(self, update, context: ContextTypes.DEFAULT_TYPE):
        """
        example:
        /view_poll 3
        """
        message = update.message
        user = update.message.from_user
        chat_username = user['username']
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.ok()
        view_poll_result = self._view_poll(
            poll_id=poll_id, chat_username=chat_username,
            bot_username=context.bot.username
        )

        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            await error_message.call(message.reply_text)
            return False

        chat_type = update.message.chat.type
        reply_markup = None

        if chat_type == 'private':
            # create vote button for reply message
            reply_markup = ReplyKeyboardMarkup(self.build_vote_markup(
                poll_id=poll_id, user=user
            ))

        poll_message = view_poll_result.ok()
        await message.reply_text(poll_message, reply_markup=reply_markup)
        return True

    async def vote_and_report(
        self, raw_text: str, chat_username: str, message: Message
    ):
        vote_result = self._vote_for_poll(
            raw_text=raw_text, chat_username=chat_username
        )

        if vote_result.is_err():
            error_message = vote_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id: int = vote_result.ok()
        await self.do_post_vote_actions(poll_id=poll_id, message=message)

    async def do_post_vote_actions(self, poll_id: int, message: Message):
        winning_option_id = self.get_poll_winner(poll_id)

        # count number of eligible voters
        num_poll_voters = PollVoters.select().where(
            PollVoters.poll_id == poll_id
        ).count()

        # count number of people who voted
        num_poll_voted = self.get_voted_voters(poll_id).count()
        everyone_voted = num_poll_voters == num_poll_voted

        if everyone_voted:
            if winning_option_id is not None:
                winning_options = Options.select().where(
                    Options.id == winning_option_id
                )

                option_name = winning_options[0].option_name
                await message.reply_text(textwrap.dedent(f"""
                    all members voted
                    poll winner is:
                    {option_name}
                """))
            else:
                await message.reply_text(textwrap.dedent(f"""
                    all members voted
                    poll has no winner
                """))
        else:
            await message.reply_text(textwrap.dedent(f"""
                vote has been registered
                vote count: {num_poll_voted}/{num_poll_voters} 
            """))

    @staticmethod
    def get_voted_voters(poll_id: int):
        # returns all voters who voted for this poll
        return PollVoters.select().where(
            PollVoters.id.in_(Votes.select(Votes.poll_voter_id).where(
                (Votes.poll_id == poll_id) &
                (Votes.ranking == 0)
            ))
        )

    @track_errors
    async def close_poll(self, update, *args, **kwargs):
        message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.ok()
        user = message.from_user
        chat_username = user['username']

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            await message.reply_text(f'poll {poll_id} does not exist')
            return False

        if poll.creator != chat_username:
            await message.reply_text(
                'only poll creator is allowed to close poll'
            )
            return False

        Polls.update({Polls.closed: True}).where(
            Polls.id == poll.id
        ).execute()

        await message.reply_text('poll closed')

    @track_errors
    async def vote_for_poll_admin(self, update: Update, *args, **kwargs):
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
        user_id = user['id']

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

        chat_username = raw_text[:raw_text.index(' ')].strip()
        # raw_text = raw_text[raw_text.index(' ')+1:].strip()
        # print('RAW', [raw_text])

        if chat_username.startswith('@'):
            chat_username = chat_username[1:]

        if ' ' not in raw_text:
            await message.reply_text('invalid format (admin)')
            return False

        print('CHAT_USERNAME', chat_username)
        # raw_text = raw_text[raw_text.index(' ')+1:].strip()
        # print('RAW', [raw_text])

        await self.vote_and_report(raw_text, chat_username, message)

    @track_errors
    async def vote_for_poll(self, update, *args, **kwargs):
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
        chat_username = user['username']

        await self.vote_and_report(raw_text, chat_username, message)

    def _vote_for_poll(
        self, raw_text, chat_username
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
        print('RAW_VOTE_TEXT', [raw_text, chat_username])
        if ' ' not in raw_text:
            error_message.add('no poll id specified')
            return Err(error_message)

        unpack_result = self.unpack_rankings_and_poll_id(raw_text)

        if unpack_result.is_err():
            assert isinstance(unpack_result, Err)
            return unpack_result

        unpacked_result = unpack_result.ok()
        poll_id: int = unpacked_result[0]
        rankings: List[int] = unpacked_result[1]

        return self.register_vote(
            poll_id=poll_id, rankings=rankings,
            chat_username=chat_username
        )

    @staticmethod
    def parse_ranking(raw_ranking) -> int:
        raw_ranking = raw_ranking.strip()

        try:
            special_ranking = SpecialVotes.from_string(raw_ranking)
            assert special_ranking.value < 0
            return special_ranking.value
        except ValueError:
            ranking = int(raw_ranking)
            assert ranking > 0
            return ranking

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
        \s*[0-9]+ -> final ranking number
        $ -> end of string        
        """
        print('RAW', raw_arguments)
        pattern_match1 = re.match(
            '^[0-9]+:?\s+(\s*[1-9]+0*\s*>)*\s*([0-9]+|nil)$',
            raw_arguments
        )
        """
        catches input of format:
        {poll_id} {choice_1} {choice_2} ... {choice_n}

        regex breakdown:
        ^ -> start of string
        ([0-9]+):*\s* -> poll_id, optional colon
        ([1-9]+0*\s+)* -> ranking number (>0) then space
        ([0-9]+) -> final ranking number
        $ -> end of string        
        """
        pattern_match2 = re.match(
            '^([0-9]+):?\s*([1-9]+0*\s+)*([0-9]+|nil)$',
            raw_arguments
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
    def get_poll_winner(poll_id):
        num_poll_voters = PollVoters.select().where(
            PollVoters.poll_id == poll_id
        ).count()

        # get votes for the poll sorted from
        # the low ranking option (most favored)
        # to the highest ranking option (least favored)
        votes = Votes.select().where(
            Votes.poll_id == poll_id
        ).order_by(Votes.ranking.asc())

        vote_map = {}
        for vote in votes:
            voter = vote.poll_voter_id
            if voter not in vote_map:
                vote_map[voter] = RankedVote()

            option_row = vote.option_id
            if option_row is None:
                vote_value = vote.special_value
            else:
                vote_value = option_row.id

            # print('VOTE_VAL', vote_value, int(vote_value))
            vote_map[voter].add_next_choice(vote_value)

        vote_flat_map = list(vote_map.values())
        print('FLAT_MAP', vote_flat_map)

        winning_option_id = RankedChoice.ranked_choice_vote(
            vote_flat_map, num_voters=num_poll_voters
        )
        return winning_option_id

    @track_errors
    async def show_about(self, update: Update, *args, **kwargs):
        message: Message = update.message
        await message.reply_text(textwrap.dedent("""
            The source code for this bot can be found at:
            https://github.com/milselarch/RCV-tele-bot
        """))

    @track_errors
    async def show_help(self, update: Update, *args, **kwargs):
        message: Message = update.message
        await message.reply_text(textwrap.dedent("""
        /start - start bot
        /user_details - shows your username and user id
        ——————————————————
        /create_poll @user_1 @user_2 ... @user_n:
        poll title
        poll option 1
        poll option 2
        ...
        poll option m
        - creates a new poll
        ——————————————————
        /view_poll {poll_id} - shows poll details given poll_id
        ——————————————————
        /vote {poll_id}: {option_1} > {option_2} > ... > {option_n} 
        /vote {poll_id} {option_1} > {option_2} > ... > {option_n} 
        /vote {poll_id} {option_1} {option_2} ... {option_n} 

        Last option can also accept 2 special values, 0 and nil:
            > Vote 0 if you want to vote for none of the options in the poll
            > Vote nil if you want to remove yourself from the poll 

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

    @track_errors
    async def view_poll_voters(self, update, *args, **kwargs):
        """
        /view_voters {poll_id}
        :param update: 
        :param args: 
        :param kwargs: 
        :return: 
        """
        message: Message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.ok()
        user: User = message.from_user
        chat_username = user['username']
        # check if voter is part of the poll

        has_poll_access = self.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            await message.reply_text(f'You have no access to poll {poll_id}')
            return False

        poll_voters_voted = self.get_voted_voters(poll_id)
        poll_voters = PollVoters.select().where(
            PollVoters.poll_id == poll_id
        )

        voter_usernames = [
            voter.username for voter in poll_voters
        ]
        voted_usernames = [
            voter.username for voter in poll_voters_voted
        ]
        not_voted_usernames = list(
            set(voter_usernames) - set(voted_usernames)
        )

        await message.reply_text(textwrap.dedent(f"""
            voted:
            {' '.join(voted_usernames)}
            not voted:
            {' '.join(not_voted_usernames)}
        """))

    @track_errors
    async def fetch_poll_results(self, update, *args, **kwargs):
        """
        /poll_results 5
        :param update:
        :param args:
        :param kwargs:
        :return:
        """
        message = update.message
        extract_result = self.extract_poll_id(update)

        if extract_result.is_err():
            error_message = extract_result.err()
            await error_message.call(message.reply_text)
            return False

        poll_id = extract_result.ok()
        user = update.message.from_user
        chat_username = user['username']
        # check if voter is part of the poll

        has_poll_access = self.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            await message.reply_text(f'You have no access to poll {poll_id}')
            return False

        winning_option_id = self.get_poll_winner(poll_id)

        if winning_option_id is None:
            await message.reply_text('no poll winner so far')
            return False
        else:
            winning_options = Options.select().where(
                Options.id == winning_option_id
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
