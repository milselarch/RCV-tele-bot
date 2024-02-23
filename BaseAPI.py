import hmac
import secrets
import string
import time
import hashlib
import textwrap
import dataclasses
import telegram

from database import *

from typing import List
from result import Ok, Err, Result
from MessageBuilder import MessageBuilder
from SpecialVotes import SpecialVotes


@dataclasses.dataclass
class PollInfo(object):
    poll_id: int
    poll_question: str
    poll_options: List[str]
    num_poll_voters: int
    num_poll_votes: int


class BaseAPI(object):
    @staticmethod
    def get_poll_voter(poll_id: int, chat_username: str):
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

    @classmethod
    def _view_poll(
        cls, poll_id: int, chat_username: str, bot_username: str
    ) -> Result[str, MessageBuilder]:
        has_poll_access = cls.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            error_message = MessageBuilder()
            error_message.add(f'You have no access to poll {poll_id}')
            return Err(error_message)

        read_poll_result = cls.read_poll_info(
            poll_id=poll_id, chat_username=chat_username
        )

        if read_poll_result.is_err():
            return read_poll_result

        poll_info = read_poll_result.ok()
        poll_message = cls.generate_poll_info(
            poll_info.poll_id, poll_info.poll_question,
            poll_info.poll_options,
            bot_username=bot_username,
            num_voters=poll_info.num_poll_voters,
            num_votes=poll_info.num_poll_votes
        )

        return Ok(poll_message)

    @classmethod
    def read_poll_info(
        cls, poll_id: int, chat_username: str
    ) -> Result[PollInfo, MessageBuilder]:
        error_message = MessageBuilder()

        poll = Polls.select().where(Polls.id == poll_id).get()
        has_poll_access = cls.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            error_message.add(f'You have no access to poll {poll_id}')
            return Err(error_message)

        poll_option_rows = Options.select().where(
            Options.poll_id == poll.id
        ).order_by(Options.option_number)

        poll_options = [
            poll_option.option_name for poll_option in poll_option_rows
        ]
        # [poll_option.id for poll_option in poll_option_rows]

        poll_question = poll.desc
        num_poll_voters = PollVoters.select().where(
            PollVoters.poll_id == poll_id
        ).count()
        # count number of first choice votes in poll
        num_poll_votes = Votes.select().where(
            (Votes.poll_id == poll_id) &
            (Votes.ranking == 0)
        ).count()

        return Ok(PollInfo(
            poll_id=poll_id, poll_question=poll_question,
            poll_options=poll_options, num_poll_voters=num_poll_voters,
            num_poll_votes=num_poll_votes
        ))

    @classmethod
    def has_poll_access(cls, poll_id, chat_username):
        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            return False

        voter_in_poll = cls.is_poll_voter(poll_id, chat_username)
        return voter_in_poll or (poll.creator == chat_username)

    @staticmethod
    def extract_poll_id(
        update: telegram.Update
    ) -> Result[int, MessageBuilder]:
        message: telegram.Message = update.message
        raw_text = message.text.strip()
        error_message = MessageBuilder()

        if ' ' not in raw_text:
            error_message.add('no poll id specified')
            return Err(error_message)

        raw_poll_id = raw_text[raw_text.index(' '):].strip()

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            error_message.add(f'invalid poll id: {raw_poll_id}')
            return Err(error_message)

        return Ok(poll_id)

    @staticmethod
    def generate_poll_info(
        poll_id, poll_question, poll_options, bot_username,
        num_votes=0, num_voters=0
    ):
        numbered_poll_options = [
            f'{k + 1}. {poll_option}' for k, poll_option,
            in enumerate(poll_options)
        ]

        args = f'poll_id={poll_id}'
        stamp = int(time.time())
        deep_link_url = (
            f'https://t.me/{bot_username}?start={args}&stamp={stamp}'
        )

        return (
            textwrap.dedent(f"""
            POLL ID: {poll_id}
            POLL QUESTION: 
            {poll_question}
            ——————————————————
            {num_votes} / {num_voters} voted
            ——————————————————
        """) + f'\n'.join(numbered_poll_options) +
            f'\n——————————————————'
            f'\nvote on the webapp at {deep_link_url}'
        )

    @staticmethod
    def make_data_check_string(
        auth_date: str, query_id: str, user: str
    ) -> str:
        data_check_string = "\n".join([
            f'auth_date={auth_date}',
            f'query_id={query_id}', f'user={user}'
        ])

        return data_check_string

    @staticmethod
    def sign_data_check_string(
        data_check_string: str, bot_token: str
    ) -> str:
        secret_key = hmac.new(
            key=b"WebAppData", msg=bot_token.encode(),
            digestmod=hashlib.sha256
        ).digest()

        validation_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        return validation_hash

    @staticmethod
    def generate_secret(pwd_length=32):
        letters = string.ascii_letters
        special_chars = string.punctuation
        digits = string.digits
        alphabet = letters + digits + special_chars

        pwd = ''
        for _ in range(pwd_length):
            pwd += ''.join(secrets.choice(alphabet))

        return pwd

    @classmethod
    def register_vote(
        cls, poll_id: int, rankings: List[int], chat_username: str
    ) -> Result[int, MessageBuilder]:
        """
        registers a vote for the poll
        checks if poll_id is valid, or if the
        poll_voter_id is valid for the poll

        :param poll_id:
        :param rankings:
        :param chat_username: chat username of voter
        :return: true if vote was registered, false otherwise
        """
        error_message = MessageBuilder()
        if len(rankings) == 0:
            error_message.add('At least one ranking must be provided')
            return Err(error_message)

        # check if voter is part of the poll
        poll_voter = cls.get_poll_voter(poll_id, chat_username)
        print('CC', poll_voter.count(), [chat_username, poll_id])

        if poll_voter.count() == 0:
            error_message.add(f"You're not a voter of poll {poll_id}")
            return Err(error_message)

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            error_message.add(f'Poll {poll_id} does not exist')
            return Err(error_message)

        if poll.closed:
            error_message.add('Poll has already been closed')
            return Err(error_message)

        poll_voter_id = poll_voter[0].id
        # print('POLL_VOTER_ID', poll_voter_id)
        vote_register_result = cls.__unsafe_register_vote(
            poll_id, poll_voter_id=poll_voter_id,
            rankings=rankings
        )

        if vote_register_result.is_err():
            assert isinstance(vote_register_result, Err)
            return vote_register_result

        vote_registered = vote_register_result.ok()

        if vote_registered:
            return Ok(poll_id)
        else:
            error_message.add('Vote registration failed')
            return Err(error_message)

    @classmethod
    def __unsafe_register_vote(
        cls, poll_id: int, poll_voter_id: int, rankings: List[int]
    ) -> Result[bool, MessageBuilder]:
        """
        registers a vote for the poll
        does not check if poll_id is valid, or if the
        poll_voter_id is valid for the poll

        :param poll_id:
        :param poll_voter_id:
        :param rankings:
        :return: true if vote was registered, false otherwise
        """
        error_message = MessageBuilder()
        poll_option_rows = Options.select().where(
            Options.poll_id == poll_id
        ).order_by(Options.option_number)

        poll_votes = []
        for ranking, choice in enumerate(rankings):
            poll_option_id, special_vote_val = None, None

            if choice > 0:
                try:
                    # specified vote choice is not in the list
                    # of available choices
                    poll_option_row = poll_option_rows[choice - 1]
                except IndexError:
                    error_message.add(f'invalid vote number: {choice}')
                    return Err(error_message)

                poll_option_id = poll_option_row.id
            else:
                # vote is a special value (0 or nil vote)
                # which gets translated to a negative integer here
                try:
                    SpecialVotes(choice)
                except ValueError:
                    error_message.add(f'invalid special vote: {choice}')
                    return Err(error_message)

                special_vote_val = choice

            poll_vote = cls.kwargify(
                poll_id=poll_id, poll_voter_id=poll_voter_id,
                option_id=poll_option_id, special_value=special_vote_val,
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

        return Ok(True)

    @staticmethod
    def kwargify(**kwargs):
        return kwargs