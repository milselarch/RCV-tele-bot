import ast
import hmac
import secrets
import string
import time
import hashlib
import textwrap
import dataclasses
import RankedChoice
import telegram
import asyncio
import redis

from typing_extensions import Any
from RankedVote import RankedVote
from database import *

from typing import List, Dict, Optional
from result import Ok, Err, Result
from MessageBuilder import MessageBuilder
from SpecialVotes import SpecialVotes


@dataclasses.dataclass
class PollInfo(object):
    poll_id: int
    poll_question: str
    # description of each option within the poll
    poll_options: List[str]
    num_poll_voters: int
    num_poll_votes: int
    # numerical ranking of each option within the poll
    option_numbers: List[int]


class BaseAPI(object):
    POLL_WINNER_KEY = "POLL_WINNER"

    def __init__(self):
        self.redis_cache = redis.Redis()
        self.cache_lock = asyncio.Lock()

    @staticmethod
    def _build_cache_key(header: str, key: str):
        return f"{header}:{key}"

    def _build_poll_winner_cache_key(self, poll_id: int) -> str:
        assert isinstance(poll_id, int)
        return self._build_cache_key(
            self.__class__.POLL_WINNER_KEY, str(poll_id)
        )

    @staticmethod
    def get_poll_closed(poll_id: int) -> Result[int, MessageBuilder]:
        error_message = MessageBuilder()

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            error_message.add(f'poll {poll_id} does not exist')
            return Err(error_message)

        if not poll.closed:
            error_message.add(
                'poll votes can only be viewed after closing'
            )
            return Err(error_message)

        return Ok(poll_id)

    async def get_poll_winner(self, poll_id: int) -> Optional[int]:
        """
        Returns poll winner for specified poll
        Attempts to get poll winner from cache if it exists,
        otherwise will run the ranked choice voting computation
        and write to the redis cache before returning
        :param poll_id:
        :return:
        """
        assert isinstance(poll_id, int)
        cache_key = self._build_poll_winner_cache_key(poll_id)

        def read_cache_value() -> Result[Optional[int], bool]:
            raw_cache_value: Optional[bytes] = self.redis_cache.get(cache_key)
            if raw_cache_value is not None:
                _poll_winner = ast.literal_eval(raw_cache_value.decode())
                print('CACHE_HIT', poll_id, [_poll_winner])
                return Ok(_poll_winner)

            return Err(False)

        cache_value = read_cache_value()
        if cache_value.is_ok():
            return cache_value.unwrap()

        async with self.cache_lock:
            """
            There is a small chance that multiple coroutines
            try to hold onto the cache lock at the same time,
            which will result in a later coroutine writing the cache
            value again after the earlier coroutine has finished.
            To prevent this, we check the cache again when the lock
            is held to guarantee that the cache value is only written once
            """
            cache_value = read_cache_value()
            if cache_value.is_ok():
                return cache_value.unwrap()

            poll_winner = self._determine_poll_winner(poll_id)
            success = self.redis_cache.set(cache_key, str(poll_winner))
            print('CACHE_SET', poll_id, [poll_winner], success)
            return poll_winner

    @staticmethod
    def _determine_poll_winner(poll_id: int) -> Optional[int]:
        """
        Runs the ranked choice voting algorithm to determine
        the winner of the poll
        :param poll_id:
        :return:
        ID of winning option, or None if there's no winner
        """
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
        winning_option_id: Optional[int] = RankedChoice.ranked_choice_vote(
            vote_flat_map, num_voters=num_poll_voters
        )

        return winning_option_id

    @staticmethod
    def get_poll_voter(poll_id: int, chat_username: str):
        # check if voter is part of the poll
        return PollVoters.select().where(
            (PollVoters.poll_id == poll_id) &
            (PollVoters.username == chat_username)
        )

    @staticmethod
    def check_has_voted(poll_id: int, chat_username: str) -> bool:
        # check if the user has voted for poll {poll_id}
        with db.atomic():
            # Perform selection operation first
            poll_voter_ids = PollVoters.select(
                PollVoters.id
            ).where(PollVoters.username == chat_username)

            # Use the selected poll_voter_ids to filter Votes table
            has_voted = bool(Votes.select().where(
                (Votes.poll_id == poll_id) &
                (Votes.poll_voter_id.in_(poll_voter_ids))
            ).count())

            return has_voted

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

        poll_info = read_poll_result.unwrap()
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
        poll_option_rankings = [
            poll_option.option_number for poll_option in poll_option_rows
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

        return Ok(PollInfo(
            poll_id=poll_id, poll_question=poll_question,
            poll_options=poll_options, num_poll_voters=num_poll_voters,
            num_poll_votes=num_poll_votes,
            option_numbers=poll_option_rankings
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
    def validate_rankings(
        cls, rankings: List[int]
    ) -> Result[bool, MessageBuilder]:
        error_message = MessageBuilder()

        print('rankings =', rankings)
        if len(rankings) != len(set(rankings)):
            error_message.add('vote rankings must be unique')
            return Err(error_message)

        non_last_rankings = rankings[:-1]
        if (len(non_last_rankings) > 0) and (min(non_last_rankings) < 1):
            error_message.add(
                'vote rankings must be positive non-zero numbers'
            )
            return Err(error_message)

        return Ok(True)

    @classmethod
    def register_vote(
        cls, poll_id: int, rankings: List[int], chat_username: str
    ) -> Result[int, MessageBuilder]:
        """
        registers a vote for the poll
        checks that:
        -   poll_id is valid,
        -   poll_voter_id corresponds to a valid voter for the poll
        -   option ranking numbers are unique
        -   rankings are non-empty
        checked internally by __unsafe_register_vote:
        -   all option ranking numbers are actually part of the poll

        :param poll_id:
        :param rankings:
        :param chat_username: chat username of voter
        """
        error_message = MessageBuilder()
        if len(rankings) == 0:
            error_message.add('At least one ranking must be provided')
            return Err(error_message)

        validate_result = cls.validate_rankings(rankings)
        if validate_result.is_err():
            return validate_result

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

        poll_voter_id: int = poll_voter[0].id
        # print('POLL_VOTER_ID', poll_voter_id)
        vote_register_result = cls.__unsafe_register_vote(
            poll_id, poll_voter_id=poll_voter_id,
            rankings=rankings
        )

        if vote_register_result.is_err():
            assert isinstance(vote_register_result, Err)
            return vote_register_result

        vote_registered = vote_register_result.unwrap()

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
        checks that poll option numbers are valid for the current poll
        does not check if poll_voter_id is valid for poll {poll_id}
        does not check if poll has already been closed

        :param poll_id:
        :param poll_voter_id:
        :param rankings:
        """
        error_message = MessageBuilder()
        poll_option_rows = Options.select().where(
            Options.poll_id == poll_id
        ).order_by(Options.option_number)

        # map poll option ranking numbers to option ids
        option_rank_to_ids: Dict[int, int] = {}
        for poll_option_row in poll_option_rows:
            option_no = poll_option_row.option_number
            option_rank_to_ids[option_no] = poll_option_row.id

        poll_votes: List[Dict[str, Any]] = []

        for ranking, option_no in enumerate(rankings):
            assert isinstance(option_no, int)
            poll_option_id: Optional[int] = None
            special_vote_val: Optional[int] = None

            if option_no > 0:
                try:
                    poll_option_id = option_rank_to_ids[option_no]
                except KeyError:
                    # specified vote choice is not in the list
                    # of available choices
                    error_message.add(f'invalid vote number: {option_no}')
                    return Err(error_message)
            else:
                # vote is a special value (withhold or abstain vote)
                # which gets translated to a negative integer here
                try:
                    SpecialVotes(option_no)
                except ValueError:
                    error_message.add(f'invalid special vote: {option_no}')
                    return Err(error_message)

                special_vote_val = option_no

            assert (
                isinstance(poll_option_id, int) or
                isinstance(special_vote_val, int)
            )
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
