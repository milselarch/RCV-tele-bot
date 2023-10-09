import asyncio
import logging
import database
import telegram
import traceback
import textwrap
import yaml
import re

import ranked_choice

from database import *
from telegram.ext import Updater, CommandHandler

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


class RankedChoiceBot(object):
    def __init__(self, config_path='config.yml'):
        self.config_path = config_path

        self.bot = None
        self.updater = None
        self.yaml_config = None

    def start_bot(self):
        with open(self.config_path, 'r') as config_file_obj:
            yaml_config = yaml.safe_load(config_file_obj)
            self.yaml_config = yaml_config
            api_key = yaml_config['telegram']['bot_token']
            self.bot = telegram.Bot(token=api_key)

        self.updater = Updater(api_key, use_context=True)

        # Get the dispatcher to register handlers
        dp = self.updater.dispatcher
        # on different commands - answer in Telegram
        self.register_commands(dp, commands_mapping=self.kwargify(
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
            help=self.show_help,

            vote_admin=self.vote_for_poll_admin,
            unclose_poll_admin=self.unclose_poll_admin,
            close_poll_admin=self.close_poll_admin
        ))

        # log all errors
        dp.add_error_handler(error_logger)
        self.updater.start_polling()

    @track_errors
    def start_handler(self, update, *args):
        # Send a message when the command /start is issued.
        update.message.reply_text('Bot started')

    @track_errors
    def name_id_handler(self, update, *args):
        """
        returns current user id and username
        """
        # when command /user_details is invoked
        user = update.message.from_user
        update.message.reply_text(textwrap.dedent(f"""
            user id: {user['id']}
            username: {user['username']}
        """))

    @track_errors
    def has_voted(self, update, *args, **kwargs):
        """
        usage:
        /has_voted {poll_id}
        """
        message = update.message
        user = update.message.from_user
        chat_username = user['username']

        poll_id = self.extract_poll_id(update)
        if poll_id is None:
            return False

        is_voter = self.is_poll_voter(
            poll_id=poll_id, chat_username=chat_username
        )

        if not is_voter:
            message.reply_text(f"You're not a voter of poll {poll_id}")
            return False

        has_voted = bool(Votes.select().join(
            PollVoters, on=(Votes.poll_voter_id == PollVoters.id)
        ).where(
            (Votes.poll_id == poll_id) &
            (PollVoters.username == chat_username)
        ).count())

        if has_voted:
            message.reply_text("you've voted already")
        else:
            message.reply_text("you haven't voted")

    @track_errors
    def create_poll(self, update, *args, **kwargs):
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
            message.reply_text("poll creation format wrong")
            return False

        split_index = raw_text.index(':')
        # first part of command is all the users that are in the poll
        command_p1 = raw_text[:split_index].strip()
        # second part of command is the poll question + poll options
        command_p2 = raw_text[split_index + 1:].strip()

        lines = command_p2.split('\n')
        if len(lines) < 3:
            message.reply_text('Poll requires at least 2 options')
            return False

        poll_question = lines[0].strip().replace('\n', '')
        poll_options = lines[1:]
        poll_options = [
            poll_option.strip().replace('\n', '')
            for poll_option in poll_options
        ]

        # print('COMMAND_P2', lines)

        if ' ' in command_p1:
            command_p1 = command_p1[command_p1.index(' '):].strip()
        else:
            message.reply_text('poll voters not specified!')

        poll_usernames = command_p1.split()
        poll_users = []

        for poll_user in poll_usernames:
            # TODO: find a way to get all users in a telegram group
            # telegram usernames must be at least 4 characters long
            if poll_user == 'all':
                if message.chat.type != 'group':
                    message.reply_text('can only add all users in a group')
                    return False
                else:
                    message.reply_text(
                        'adding all users in a group is not suppoerted'
                    )
                    return False
            else:
                if poll_user.startswith('@'):
                    poll_user = poll_user[1:]
                if len(poll_user) < 4:
                    message.reply_text(f'username too short: {poll_user}')
                    return False

                poll_users.append(poll_user)

        new_poll = Polls.create(
            desc=poll_question, creator=creator_username
        )

        new_poll.save()
        new_poll_id = new_poll.id
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
        print('GROUND_ID', group_id)
        chat = Chats.create(
            poll_id=new_poll_id, tele_id=group_id,
            broadcasted=False
        )

        with db.atomic():
            Options.insert_many(poll_option_rows).execute()
            PollVoters.insert_many(poll_user_rows).execute()
            chat.save()

        poll_message = self.generate_poll_info(
            new_poll_id, poll_question, poll_options,
            num_voters=len(poll_users)
        )

        message.reply_text(poll_message)

    @staticmethod
    def generate_poll_info(
            poll_id, poll_question, poll_options,
            num_votes=0, num_voters=0
    ):
        numbered_poll_options = [
            f'{k + 1}. {poll_option}' for k, poll_option
            in enumerate(poll_options)
        ]

        return textwrap.dedent(f"""
            POLL ID: {poll_id}
            POLL QUESTION: 
            {poll_question}
            ——————————————————
            {num_votes} / {num_voters} voted
            ——————————————————
        """) + f'\n'.join(numbered_poll_options)

    @staticmethod
    def get_poll_voter(poll_id, chat_username):
        # check if voter is part of the poll
        voter = PollVoters.select().join(
            Polls, on=(Polls.id == PollVoters.poll_id)
        ).where(
            (Polls.id == poll_id) &
            (PollVoters.username == chat_username)
        )

        return voter

    @classmethod
    def is_poll_voter(cls, *args, **kwargs):
        return cls.get_poll_voter(*args, **kwargs).count() > 0

    @staticmethod
    def extract_poll_id(update):
        message = update.message
        raw_text = message.text.strip()

        if ' ' not in raw_text:
            message.reply_text('no poll id specified')
            return None

        raw_poll_id = raw_text[raw_text.index(' '):].strip()

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            message.reply_text(f'invalid poll id: {raw_poll_id}')
            return None

        return poll_id

    def has_poll_access(self, poll_id, chat_username):
        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            return False

        voter_in_poll = self.is_poll_voter(poll_id, chat_username)
        return voter_in_poll or (poll.creator == chat_username)

    @track_errors
    def view_votes(self, update, *args, **kwargs):
        poll_id = self.extract_poll_id(update)
        if poll_id is None:
            return False

        message = update.message
        user = update.message.from_user
        chat_username = user['username']
        # check if voter is part of the poll

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            message.reply_text(f'poll {poll_id} does not exist')
            return False

        if not poll.closed:
            message.reply_text('poll votes can only be viewed after closing')
            return False

        has_poll_access = self.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            message.reply_text(f'You have no access to poll {poll_id}')
            return False

        # get poll options in ascending order
        poll_option_rows = Options.select().where(
            Options.poll_id == poll_id
        ).order_by(Options.option_number)

        # map poll option ids to their option numbers
        option_index_map = {}
        for poll_option_row in poll_option_rows:
            option_index_map[poll_option_row.id] = (
                poll_option_row.option_number
            )

        vote_rows = (Votes.select()
            .where(Votes.poll_id == poll_id)
            .order_by(Votes.option_id, Votes.ranking)
        )

        vote_sequence_map = {}
        for vote_row in vote_rows:
            voter_id = vote_row.poll_voter_id

            if voter_id not in vote_sequence_map:
                vote_sequence_map[voter_id] = {}

            option_id = vote_row.option_id
            vote_sequence_map[voter_id][vote_row.ranking] = option_id

        ranking_message = ''
        for voter_id in vote_sequence_map:
            ranking_map = vote_sequence_map[voter_id]
            ranking_nos = sorted(ranking_map.keys())
            sorted_option_nos = [
                ranking_map[ranking] for ranking in ranking_nos
            ]

            print('SORT-NOS', sorted_option_nos)
            ranking_message += ' > '.join([
                str(option_index_map[option_id.id])
                for option_id in sorted_option_nos
            ]).strip() + '\n'

        ranking_message = ranking_message.strip()
        message.reply_text(f'votes recorded:\n{ranking_message}')

    @track_errors
    def unclose_poll_admin(self, update, *args, **kwargs):
        self._set_poll_status(update, False)

    @track_errors
    def close_poll_admin(self, update, *args, **kwargs):
        self._set_poll_status(update, True)

    def _set_poll_status(self, update, closed=True):
        message = update.message
        user = update.message.from_user
        user_id = user['id']

        if user_id != self.yaml_config['telegram']['sudo_id']:
            message.reply_text('ACCESS DENIED')
            return False

        poll_id = self.extract_poll_id(update)
        if poll_id is None:
            return False

        Polls.update({Polls.closed: closed}).where(
            Polls.id == poll_id
        ).execute()

        message.reply_text(f'poll {poll_id} has been unclosed')

    @track_errors
    def view_poll(self, update, *args, **kwargs):
        """
        example:
        /view_poll 3
        """
        message = update.message
        user = update.message.from_user
        chat_username = user['username']

        poll_id = self.extract_poll_id(update)
        if poll_id is None:
            return False

        poll = Polls.select().where(Polls.id == poll_id).get()
        has_poll_access = self.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            message.reply_text(f'You have no access to poll {poll_id}')
            return False

        poll_option_rows = Options.select().where(
            Options.poll_id == poll.id
        ).order_by(Options.option_number)

        poll_options = [
            poll_option.option_name for poll_option in poll_option_rows
        ]

        poll_question = poll.desc
        num_poll_voters = PollVoters.select().where(
            PollVoters.poll_id == poll_id
        ).count()
        # count number of first choice votes in poll
        num_poll_votes = Votes.select().where(
            (Votes.poll_id == poll_id) &
            (Votes.ranking == 0)
        ).count()

        poll_message = self.generate_poll_info(
            poll_id, poll_question, poll_options,
            num_voters=num_poll_voters,
            num_votes=num_poll_votes
        )

        # print('POLL_OPTIONS', poll_options, poll.id)
        message.reply_text(poll_message)

    def vote_and_report(self, raw_text, chat_username, message):
        vote_result = self._vote_for_poll(
            raw_text=raw_text, chat_username=chat_username,
            message=message
        )

        if vote_result is False:
            return

        poll_id = vote_result
        winning_option_id = self.get_poll_winner(poll_id)

        # count number of eligible voters
        num_poll_voters = PollVoters.select().where(
            PollVoters.poll_id == poll_id
        ).count()
        # count number of people who voted
        num_poll_voted = PollVoters.select().join(
            Votes, on=(Votes.poll_voter_id == PollVoters.id)
        ).where(
            (PollVoters.poll_id == poll_id) &
            (Votes.ranking == 0)
        ).count()

        everyone_voted = num_poll_voters == num_poll_voted

        if everyone_voted:
            if winning_option_id is not None:
                winning_options = Options.select().where(
                    Options.id == winning_option_id
                )

                option_name = winning_options[0].option_name
                message.reply_text(textwrap.dedent(f"""
                    all members voted
                    poll winner is:
                    {option_name}
                """))
            else:
                message.reply_text(textwrap.dedent(f"""
                    all members voted
                    poll has no winner
                """))
        else:
            message.reply_text(textwrap.dedent(f"""
                vote has been registered
                vote count: {num_poll_voted}/{num_poll_voters} 
            """))

    @track_errors
    def close_poll(self, update, *args, **kwargs):
        poll_id = self.extract_poll_id(update)
        if poll_id is None:
            return False

        message = update.message
        user = update.message.from_user
        chat_username = user['username']

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            message.reply_text(f'poll {poll_id} does not exist')
            return False

        if poll.creator != chat_username:
            message.reply_text('only poll creator is allowed to close poll')
            return False

        Polls.update({Polls.closed: True}).where(
            Polls.id == poll.id
        ).execute()

        message.reply_text('poll closed')

    @track_errors
    def vote_for_poll_admin(self, update, *args, **kwargs):
        """
        telegram command format
        /vote_admin {username} {poll_id}: {option_1} > ... > {option_n}
        example:
        /vote 3: 1 > 2 > 3
        """
        # vote for someone else
        message = update.message
        raw_text = message.text.strip()
        user = update.message.from_user
        user_id = user['id']

        if user_id != self.yaml_config['telegram']['sudo_id']:
            message.reply_text('ACCESS DENIED')
            return False

        if ' ' not in raw_text:
            message.reply_text('no user specified')
            return False

        raw_text = raw_text[raw_text.index(' ')+1:].strip()
        if ' ' not in raw_text:
            message.reply_text('no poll_id specified (admin)')
            return False

        chat_username = raw_text[:raw_text.index(' ')].strip()
        # raw_text = raw_text[raw_text.index(' ')+1:].strip()
        # print('RAW', [raw_text])

        if chat_username.startswith('@'):
            chat_username = chat_username[1:]

        if ' ' not in raw_text:
            message.reply_text('invalid format (admin)')
            return False

        print('CHAT_USERNAME', chat_username)
        # raw_text = raw_text[raw_text.index(' ')+1:].strip()
        # print('RAW', [raw_text])

        self.vote_and_report(raw_text, chat_username, message)

    @track_errors
    def vote_for_poll(self, update, *args, **kwargs):
        """
        telegram command format
        /vote {poll_id}: {option_1} > {option_2} > ... > {option_n}
        example:
        /vote 3: 1 > 2 > 3
        """
        message = update.message
        raw_text = message.text.strip()
        user = update.message.from_user
        chat_username = user['username']

        self.vote_and_report(raw_text, chat_username, message)

    def _vote_for_poll(self, raw_text, chat_username, message):
        """
        telegram command format
        /vote {poll_id}: {option_1} > {option_2} > ... > {option_n}
        example:
        /vote 3: 1 > 2 > 3
        """
        print('RAW_VOTE_TEXT', [raw_text, chat_username])
        if ' ' not in raw_text:
            message.reply_text('no poll id specified')
            return False

        unpack_result = self.unpack_rankings_and_poll_id(raw_text, message)
        if unpack_result is False:
            return False

        poll_id, rankings = unpack_result
        # check if voter is part of the poll
        poll_voter = self.get_poll_voter(poll_id, chat_username)
        print('CC', poll_voter.count(), [chat_username, poll_id])

        if poll_voter.count() == 0:
            message.reply_text(f"You're not a voter of poll {poll_id}")
            return False

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            message.reply_text(f'poll {poll_id} does not exist')
            return False

        if poll.closed:
            message.reply_text('poll has already been closed')
            return False

        poll_voter_id = poll_voter[0].id
        # print('POLL_VOTER_ID', poll_voter_id)

        vote_registered = self.register_vote(
            poll_id, poll_voter_id=poll_voter_id,
            rankings=rankings, message=message
        )

        if not vote_registered:
            return False

        return poll_id

    @staticmethod
    def unpack_rankings_and_poll_id(raw_text, message):
        """
        raw_text format:
        {command} {poll_id}: {choice_1} > {choice_2} > ... > {choice_n}
        """
        # remove starting command from raw_text
        raw_arguments = raw_text[raw_text.index(' '):].strip()

        """
        catches input of format:
        {poll_id}: {choice_1} > {choice_2} > ... > {choice_n}
        
        regex breakdown:
        ^ -> start of string
        [0-9]+:\s* -> poll_id and colon (and optional space)
        (\s*[0-9]+\s*>)* -> ranking number then arrow
        \s*[0-9]+ -> final ranking number
        $ -> end of string
        """
        print('RAW', raw_arguments)
        pattern_match1 = re.match(
            '^[0-9]+:\s*(\s*[0-9]+\s*>)*\s*[0-9]+$', raw_arguments
        )
        """
        catches input of format:
        {poll_id} {choice_1} > {choice_2} > ... > {choice_n}
        
        regex breakdown:
        ^ -> start of string
        [0-9]+\s+ -> poll_id and space 
        (\s*[0-9]+\s*>)* -> ranking number then arrow
        \s*[0-9]+ -> final ranking number
        $ -> end of string        """
        pattern_match2 = re.match(
            '^[0-9]+\s+(\s*[0-9]+\s*>)*\s*[0-9]+$', raw_arguments
        )

        if pattern_match1:
            raw_arguments = raw_arguments.replace(' ', '')
            raw_poll_id, raw_votes = raw_arguments.split(':')
            raw_poll_id = raw_poll_id.strip()
            raw_votes = raw_votes.strip()
        elif pattern_match2:
            seperator_index = raw_arguments.index(' ')
            raw_poll_id = int(raw_arguments[:seperator_index])
            raw_votes = raw_arguments[seperator_index:].strip()
        else:
            message.reply_text('input format is invalid')
            return False

        rankings = [int(ranking) for ranking in raw_votes.split('>')]
        # print('rankings =', rankings)

        if len(rankings) != len(set(rankings)):
            message.reply_text('vote rankings must be unique')
            return False
        if min(rankings) < 1:
            message.reply_text(
                'vote rankings must be positive non-zero numbers'
            )
            return False

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            message.reply_text(f'invalid poll id: {raw_arguments}')
            return False

        return poll_id, rankings

    def register_vote(
        self, poll_id, poll_voter_id, rankings, message=None
    ):
        """
        :param poll_id:
        :param poll_voter_id:
        :param rankings:
        :param message: telegram message object
        :return: true if vote was registered, false otherwise
        """
        poll_option_rows = Options.select().where(
            Options.poll_id == poll_id
        ).order_by(Options.option_number)

        poll_votes = []
        for ranking, choice in enumerate(rankings):
            try:
                # specified vote choice is not in the list
                # of available choices
                poll_option_row = poll_option_rows[choice - 1]
            except IndexError:
                if message is not None:
                    message.reply_text(f'invalid vote number: {choice}')

                return False

            poll_vote = self.kwargify(
                poll_id=poll_id, poll_voter_id=poll_voter_id,
                option_id=poll_option_row.id,
                ranking=ranking
            )

            poll_votes.append(poll_vote)

        # clear previous vote by the same user on the same poll
        delete_vote_query = Votes.delete().where(
            (Votes.poll_voter_id == poll_voter_id) &
            (Votes.poll_id == poll_id)
        )

        with db.atomic():
            delete_vote_query.execute()
            Votes.insert_many(poll_votes).execute()

        return True

    @staticmethod
    def get_poll_winner(poll_id):
        num_poll_voters = PollVoters.select().where(
            PollVoters.poll_id == poll_id
        ).count()

        votes = Votes.select().where(
            Votes.poll_id == poll_id
        ).order_by(Votes.ranking.desc())

        vote_map = {}
        for vote in votes:
            voter = vote.poll_voter_id

            if voter not in vote_map:
                vote_map[voter] = []

            vote_map[voter].append(vote.option_id)

        vote_flat_map = list(vote_map.values())
        print('FLAT_MAP', vote_flat_map)

        winning_option_id = ranked_choice.ranked_choice_vote(
            vote_flat_map, num_voters=num_poll_voters
        )
        return winning_option_id

    @track_errors
    def show_help(self, update, *args, **kwargs):
        message = update.message
        message.reply_text(textwrap.dedent("""
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
        /help - view commands available to the bot
        """))

    def view_poll_voters(self, update, *args, **kwargs):
        """
        /view_voters {poll_id}
        :param update: 
        :param args: 
        :param kwargs: 
        :return: 
        """
        poll_id = self.extract_poll_id(update)
        if poll_id is None:
            return False

        message = update.message
        user = update.message.from_user
        chat_username = user['username']
        # check if voter is part of the poll

        has_poll_access = self.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            message.reply_text(f'You have no access to poll {poll_id}')
            return False

        poll_voters_voted = PollVoters.select().join(
            Votes, on=(Votes.poll_voter_id == PollVoters.id)
        ).where(
            (PollVoters.poll_id == poll_id) &
            (Votes.ranking == 0)
        )
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

        message.reply_text(textwrap.dedent(f"""
            voted:
            {' '.join(voted_usernames)}
            not voted:
            {' '.join(not_voted_usernames)}
        """))

    @track_errors
    def fetch_poll_results(self, update, *args, **kwargs):
        """
        /poll_results 5
        :param update:
        :param args:
        :param kwargs:
        :return:
        """
        poll_id = self.extract_poll_id(update)
        if poll_id is None:
            return False

        message = update.message
        user = update.message.from_user
        chat_username = user['username']
        # check if voter is part of the poll

        has_poll_access = self.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            message.reply_text(f'You have no access to poll {poll_id}')
            return False

        winning_option_id = self.get_poll_winner(poll_id)

        if winning_option_id is None:
            message.reply_text('no poll winner so far')
            return False
        else:
            winning_options = Options.select().where(
                Options.id == winning_option_id
            )

            option_name = winning_options[0].option_name
            message.reply_text(f'poll winner is:\n{option_name}')

    @staticmethod
    def kwargify(**kwargs):
        return kwargs

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
