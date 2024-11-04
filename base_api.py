import asyncio
import hmac
import json
import re
import secrets
import string

import time
import hashlib
import textwrap
import dataclasses
import telegram
import database
import aioredlock

from enum import IntEnum
from typing_extensions import Any
from strenum import StrEnum

from helpers.subscription_tiers import SubscriptionTiers
from load_config import TELEGRAM_BOT_TOKEN
from telegram.ext import ApplicationBuilder
from py_rcv import VotesCounter as PyVotesCounter

from typing import List, Dict, Optional, Tuple
from result import Ok, Err, Result
from concurrent.futures import ThreadPoolExecutor
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from helpers.message_buillder import MessageBuilder
from helpers.special_votes import SpecialVotes
from database import (
    Polls, PollVoters, UsernameWhitelist, PollOptions, VoteRankings,
    db, Users
)
from database.database import PollWinners, BaseModel, UserID, PollMetadata
from aioredlock import Aioredlock, LockError


class CallbackCommands(StrEnum):
    REGISTER = 'REGISTER'
    DELETE = 'DELETE'


class UserRegistrationStatus(StrEnum):
    REGISTERED = 'REGISTERED'
    ALREADY_REGISTERED = 'ALREADY_REGISTERED'
    INVALID_SUBSCRIPTION_TIER = 'INVALID_SUBSCRIPTION_TIER'
    USER_NOT_FOUND = 'USER_NOT_FOUND'
    POLL_NOT_FOUND = 'POLL_NOT_FOUND'
    VOTER_LIMIT_REACHED = 'VOTER_LIMIT_REACHED'
    NOT_WHITELISTED = 'NOT_WHITELISTED'
    """
    Another user (different user_tele_id) has already registered for the
    same poll using the same username
    """
    POLL_CLOSED = 'POLL_CLOSED'
    USERNAME_TAKEN = 'USERNAME_TAKEN'
    FAILED = 'FAILED'


@dataclasses.dataclass
class PollMessage(object):
    text: str
    reply_markup: Optional[InlineKeyboardMarkup]


class GetPollWinnerStatus(IntEnum):
    CACHED = 0
    NEWLY_COMPUTED = 1
    COMPUTING = 2
    FAILED = 3


@dataclasses.dataclass
class PollInfo(object):
    metadata: PollMetadata
    # description of each option within the poll
    poll_options: List[str]
    # numerical ranking of each option within the poll
    option_numbers: List[int]


class BaseAPI(object):
    POLL_WINNER_KEY = "POLL_WINNER"
    POLL_WINNER_LOCK_KEY = "POLL_WINNER_LOCK"
    # CACHE_LOCK_NAME = "REDIS_CACHE_LOCK"
    POLL_CACHE_EXPIRY = 60
    DELETION_TOKEN_EXPIRY = 60 * 5
    SHORT_HASH_LENGTH = 6

    def __init__(self):
        database.initialize_db()
        self.redis_lock_manager = self.create_redis_lock_manager()

    @staticmethod
    def create_redis_lock_manager(
        connections: list[dict[str, str | int]] | None = None
    ):
        if connections is not None:
            return Aioredlock(connections)
        else:
            return Aioredlock()

    @staticmethod
    def __get_telegram_token():
        # TODO: move methods using tele token to a separate class
        return TELEGRAM_BOT_TOKEN

    def generate_delete_token(self, user: Users):
        stamp = int(time.time())
        hex_stamp = hex(stamp)[2:].upper()
        user_id = user.get_user_id()
        hash_input = f'{user_id}:{stamp}'

        signed_message = self.sign_message(hash_input).upper()
        short_signed_message = signed_message[:self.SHORT_HASH_LENGTH]
        return f'{hex_stamp}:{short_signed_message}'

    def validate_delete_token(
        self, user: Users, stamp: int, short_hash: str
    ) -> Result[bool, str]:
        current_stamp = int(time.time())
        if abs(current_stamp - stamp) > self.DELETION_TOKEN_EXPIRY:
            return Err('Token expired')

        user_id = user.get_user_id()
        hash_input = f'{user_id}:{stamp}'
        signed_message = self.sign_message(hash_input).upper()
        short_signed_message = signed_message[:self.SHORT_HASH_LENGTH]

        if short_signed_message != short_hash:
            return Err('Invalid token')

        return Ok(True)

    @classmethod
    def create_tele_bot(cls):
        return telegram.Bot(token=cls.__get_telegram_token())

    @classmethod
    def create_application_builder(cls):
        builder = ApplicationBuilder()
        builder.token(cls.__get_telegram_token())
        return builder

    @staticmethod
    def _build_cache_key(header: str, key: str):
        return f"{header}:{key}"

    def _build_poll_winner_lock_cache_key(self, poll_id: int) -> str:
        assert isinstance(poll_id, int)
        return self._build_cache_key(
            self.__class__.POLL_WINNER_LOCK_KEY, str(poll_id)
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
            error_message.add('poll votes can only be viewed after closing')
            return Err(error_message)

        return Ok(poll_id)

    @staticmethod
    def fetch_poll(poll_id: int) -> Result[Polls, MessageBuilder]:
        error_message = MessageBuilder()

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            error_message.add(f'poll {poll_id} does not exist')
            return Err(error_message)

        return Ok(poll)

    @classmethod
    def get_num_active_poll_voters(
        cls, poll_id: int
    ) -> Result[int, MessageBuilder]:
        result = cls.fetch_poll(poll_id)
        if result.is_err():
            return result

        poll = result.unwrap()
        return Ok(poll.num_active_voters)

    @staticmethod
    async def refresh_lock(lock: aioredlock.Lock, interval: float):
        try:
            while True:
                print('WAIT')
                await asyncio.sleep(interval)
                await lock.extend()
        except asyncio.CancelledError:
            pass

    async def get_poll_winner(
        self, poll_id: int
    ) -> Tuple[Optional[int], GetPollWinnerStatus]:
        """
        Returns poll winner for specified poll
        Attempts to get poll winner from cache if it exists,
        otherwise will run the ranked choice voting computation
        and write to the redis cache before returning
        # TODO: test that redis lock refresh works

        :param poll_id:
        :return:
        poll winner, status of poll winner computation
        """
        assert isinstance(poll_id, int)

        cache_result = PollWinners.read_poll_winner_id(poll_id)
        if cache_result.is_ok():
            # print('CACHE_HIT', cache_result)
            return cache_result.unwrap(), GetPollWinnerStatus.CACHED

        redis_lock_key = self._build_poll_winner_lock_cache_key(poll_id)
        # print('CACHE_KEY', redis_cache_key)
        if await self.redis_lock_manager.is_locked(redis_lock_key):
            # print('PRE_LOCKED')
            return None, GetPollWinnerStatus.COMPUTING

        try:
            # prevents race conditions where multiple computations
            # are run concurrently for the same poll
            async with await self.redis_lock_manager.lock(
                redis_lock_key, lock_timeout=self.POLL_CACHE_EXPIRY
            ) as lock:
                # Start a task to refresh the lock periodically
                refresh_task = asyncio.create_task(self.refresh_lock(
                    lock, self.POLL_CACHE_EXPIRY / 2
                ))

                try:
                    cache_result = PollWinners.read_poll_winner_id(poll_id)
                    if cache_result.is_ok():
                        # print('INNER_CACHE_HIT', cache_result)
                        return cache_result.unwrap(), GetPollWinnerStatus.CACHED

                    # compute the winner in a separate thread to not block
                    # the async event loop
                    loop = asyncio.get_event_loop()
                    with ThreadPoolExecutor() as executor:
                        poll_winner_id = await loop.run_in_executor(
                            executor, self._determine_poll_winner, poll_id
                        )

                    # Store computed winner in the db
                    PollWinners.build_from_fields(
                        poll_id=poll_id, option_id=poll_winner_id
                    ).get_or_create()
                finally:
                    # Cancel the refresh task
                    refresh_task.cancel()
                    await refresh_task

        except LockError:
            # print('LOCK_ERROR')
            return None, GetPollWinnerStatus.COMPUTING

        # print('CACHE_MISS', poll_winner_id)
        return poll_winner_id, GetPollWinnerStatus.NEWLY_COMPUTED

    @classmethod
    def _determine_poll_winner(cls, poll_id: int) -> Optional[int]:
        """
        Runs the ranked choice voting algorithm to determine
        the winner of the poll
        :param poll_id:
        :return:
        ID of winning option, or None if there's no winner
        """
        num_poll_voters_result = cls.get_num_active_poll_voters(poll_id)
        if num_poll_voters_result.is_err():
            return None

        num_poll_voters: int = num_poll_voters_result.unwrap()
        # get votes for the poll sorted by PollVoter and from
        # the lowest ranking option (most favored)
        # to the highest ranking option (least favored)
        votes = VoteRankings.select().join(
            PollVoters, on=(PollVoters.id == VoteRankings.poll_voter)
        ).where(
            PollVoters.poll == poll_id
        ).order_by(
            PollVoters.id, VoteRankings.ranking.asc()  # TODO: test ordering
        )

        prev_voter_id, num_votes_cast = None, 0
        votes_aggregator = PyVotesCounter()

        for vote_ranking in votes:
            option_row = vote_ranking.option
            voter_id = vote_ranking.poll_voter.id

            if prev_voter_id != voter_id:
                votes_aggregator.flush_votes()
                prev_voter_id = voter_id
                num_votes_cast += 1

            if option_row is None:
                vote_value = vote_ranking.special_value
            else:
                vote_value = option_row.id

            # print('VOTE_VAL', vote_value, int(vote_value))
            votes_aggregator.insert_vote_ranking(voter_id, vote_value)

        votes_aggregator.flush_votes()
        voters_without_votes = num_poll_voters - num_votes_cast
        assert voters_without_votes >= 0
        votes_aggregator.insert_empty_votes(voters_without_votes)
        winning_option_id = votes_aggregator.determine_winner()
        return winning_option_id

    @staticmethod
    def get_poll_voter(
        poll_id: int, user_id: UserID
    ) -> Result[PollVoters, Optional[BaseModel.DoesNotExist]]:
        # check if voter is part of the poll
        return PollVoters.safe_get(
            (PollVoters.poll == poll_id) &
            (PollVoters.user == user_id)
        )

    @classmethod
    def verify_voter(
        cls, poll_id: int, user_id: UserID, username: Optional[str] = None
    ) -> Result[int, UserRegistrationStatus]:
        """
        Checks if the user is a member of the poll
        Attempts to auto enroll user if their username is whitelisted
        and the username whitelist entry is empty
        Returns PollVoters entry id of user for the specified poll
        """
        
        poll_voter_res = cls.get_poll_voter(poll_id, user_id)
        if poll_voter_res.is_ok():
            poll_voter = poll_voter_res.unwrap()
            return Ok(poll_voter.id)
        else:
            poll_voter_err = poll_voter_res.unwrap_err()
            if poll_voter_err is None:
                return Err(UserRegistrationStatus.FAILED)

        # error_message = MessageBuilder()
        if not isinstance(username, str):
            # error_message.add(f"You're not a voter of poll {poll_id}")
            return Err(UserRegistrationStatus.NOT_WHITELISTED)

        username_str = username
        assert isinstance(username_str, str)

        whitelist_user_result = cls.get_whitelist_entry(
            username=username_str, poll_id=poll_id,
            user_id=user_id
        )
        if whitelist_user_result.is_err():
            return whitelist_user_result

        whitelisted_user = whitelist_user_result.unwrap()
        assert (
            (whitelisted_user.user is None) or
            (whitelisted_user.user == user_id)
        )

        register_result = cls._register_voter_from_whitelist(
            poll_id=poll_id, user_id=user_id,
            ignore_voter_limit=False, username=username_str
        )

        if register_result.is_err():
            return register_result
        else:
            poll_voter: PollVoters = register_result.unwrap()
            return Ok(poll_voter.id)

    @classmethod
    def _register_voter_from_whitelist(
        cls, poll_id: int, user_id: UserID, ignore_voter_limit: bool,
        username: str
    ) -> Result[PollVoters, UserRegistrationStatus]:
        """
        Given a username whitelist entry that is unoccupied:
        1.  assign a user_id to the whitelist entry
        2.  register the user_id to the list of poll voters if
            it doesn't already exist
        3.  Increment the count for the number of poll voters if
            a new poll voter entry was created
        """
        assert isinstance(username, str)
        assert isinstance(poll_id, int)
        assert isinstance(user_id, int)

        with db.atomic() as transaction:
            whitelist_user_result = cls.get_whitelist_entry(
                username=username, poll_id=poll_id,
                user_id=user_id
            )

            if whitelist_user_result.is_err():
                transaction.rollback()
                return whitelist_user_result

            whitelist_entry = whitelist_user_result.unwrap()
            whitelist_entry_id = whitelist_entry.id
            assert isinstance(whitelist_entry_id, int)
            whitelisted_user = whitelist_entry.user
            # increment number of registered voters
            whitelist_inapplicable = (
                (whitelisted_user is not None) and
                (whitelisted_user.id != user_id)
            )

            if whitelist_inapplicable:
                transaction.rollback()
                return Err(UserRegistrationStatus.USERNAME_TAKEN)

            if whitelisted_user is None:
                UsernameWhitelist.update({
                    UsernameWhitelist.user: user_id
                }).where(
                    UsernameWhitelist.id == whitelist_entry_id
                ).execute()

            register_result = cls._register_user_id(
                poll_id=poll_id, user_id=user_id,
                ignore_voter_limit=ignore_voter_limit,
                from_whitelist=True
            )

            if register_result.is_err():
                transaction.rollback()
                return register_result

            poll_voter_row, _ = register_result.unwrap()
            return Ok(poll_voter_row)

    @staticmethod
    def _reg_status_to_msg(
        registration_status: UserRegistrationStatus, poll_id: int
    ):
        match registration_status:
            case UserRegistrationStatus.REGISTERED:
                return "Registered for poll"
            case UserRegistrationStatus.ALREADY_REGISTERED:
                return "Already registered for poll"
            case UserRegistrationStatus.VOTER_LIMIT_REACHED:
                return "Voter limit reached"
            case UserRegistrationStatus.USERNAME_TAKEN:
                return textwrap.dedent(""""
                    Another user has already registered for the poll 
                    using the same username
                """)
            case UserRegistrationStatus.POLL_NOT_FOUND:
                return f"Poll #{poll_id} not found"
            case UserRegistrationStatus.POLL_CLOSED:
                return f"Poll #{poll_id} has been closed"
            case _:
                return "Unexpected registration error"

    @staticmethod
    def _register_user_id(
        poll_id: int, user_id: UserID, ignore_voter_limit: bool,
        from_whitelist: bool = False
    ) -> Result[Tuple[PollVoters, bool], UserRegistrationStatus]:
        """
        Attempts to register a user for a poll using their user_id

        :param poll_id:
        :param user_id: user id
        :param ignore_voter_limit:
        Whether to register the user even if poll voter limit is reached
        :param from_whitelist:
        Whether the voter was registered from the username whitelist.
        Doesn't increment voter count if set
        :return:
        Ok value is PollVoter row and whether new PollVoter entry was created
        """
        with db.atomic():
            try:
                poll = Polls.get(id=poll_id)
            except Polls.DoesNotExist:
                return Err(UserRegistrationStatus.POLL_NOT_FOUND)

            # print('NUM_VOTES', poll.num_voters, poll.max_voters)
            voter_limit_reached = (poll.num_active_voters >= poll.max_voters)
            if ignore_voter_limit:
                voter_limit_reached = False

            with db.atomic() as txn:
                # registers a voter by their user_id
                poll_voter, voter_row_created = PollVoters.build_from_fields(
                    user_id=user_id, poll_id=poll_id
                ).get_or_create()

                if voter_limit_reached and voter_row_created:
                    txn.rollback()
                    return Err(UserRegistrationStatus.VOTER_LIMIT_REACHED)

            if voter_row_created and not from_whitelist:
                # increment number of registered voters
                # if PollVoters entry was created
                Polls.update({
                    Polls.num_voters: Polls.num_voters + 1
                }).where(
                    Polls.id == poll_id
                ).execute()

            return Ok((poll_voter, voter_row_created))

    @classmethod
    def _register_voter(
        cls, poll_id: int, user_id: UserID, username: Optional[str]
    ) -> UserRegistrationStatus:
        """
        Registers a user by using the username whitelist if applicable,
        or by directly creating a PollVoters entry otherwise
        Does NOT validate if the user is allowed to register for the poll
        """
        poll = Polls.get_or_none(Polls.id == poll_id)
        if poll is None:
            return UserRegistrationStatus.POLL_NOT_FOUND
        elif poll.closed:
            return UserRegistrationStatus.POLL_CLOSED

        try:
            PollVoters.build_from_fields(
                user_id=user_id, poll_id=poll_id
            ).get()
            return UserRegistrationStatus.ALREADY_REGISTERED
        except PollVoters.DoesNotExist:
            pass

        try:
            user = Users.build_from_fields(user_id=user_id).get()
        except Users.DoesNotExist:
            return UserRegistrationStatus.USER_NOT_FOUND

        try:
            subscription_tier = SubscriptionTiers(user.subscription_tier)
        except ValueError:
            return UserRegistrationStatus.INVALID_SUBSCRIPTION_TIER

        has_empty_whitelist_entry = False
        ignore_voter_limit = subscription_tier != SubscriptionTiers.FREE

        if username is not None:
            assert isinstance(username, str)
            whitelist_entry_result = cls.get_whitelist_entry(
                poll_id=poll_id, user_id=user_id, username=username
            )

            # checks if there is a username whitelist entry that is unoccupied
            if whitelist_entry_result.is_ok():
                whitelist_entry = whitelist_entry_result.unwrap()

                if whitelist_entry.username == username:
                    return UserRegistrationStatus.ALREADY_REGISTERED
                elif whitelist_entry.username is None:
                    has_empty_whitelist_entry = True

        with db.atomic():
            if has_empty_whitelist_entry:
                """
                Try to register user via the username whitelist
                if there is a unoccupied username whitelist entry
                We verify again that the username whitelist entry is empty
                here because there is a small chance that the whitelist entry
                is set between the last check and acquisition of database lock
                """
                assert isinstance(username, str)
                username_str = username

                whitelist_user_result = cls.get_whitelist_entry(
                    username=username_str, poll_id=poll_id,
                    user_id=user_id
                )

                if whitelist_user_result.is_ok():
                    whitelist_entry = whitelist_user_result.unwrap()
                    assert (
                        (whitelist_entry.user is None) or
                        (whitelist_entry.user == user_id)
                    )

                    if whitelist_entry.user == user_id:
                        return UserRegistrationStatus.ALREADY_REGISTERED
                    elif whitelist_entry.user is None:
                        # print("POP", poll_id, user_id, username_str)
                        register_result = cls._register_voter_from_whitelist(
                            poll_id=poll_id, user_id=user_id,
                            ignore_voter_limit=ignore_voter_limit,
                            username=username_str
                        )

                        if register_result.is_ok():
                            return UserRegistrationStatus.REGISTERED
                        else:
                            assert register_result.is_err()
                            return register_result.err_value

                    # username whitelist entry assigned to different user_id
                    assert isinstance(whitelist_entry.user, int)
                    assert whitelist_entry.user != user_id

            """
            Register by adding user to PollVoters directly if and only if
            registration via username whitelist didn't happen
            """
            register_result = cls._register_user_id(
                poll_id=poll_id, user_id=user_id,
                ignore_voter_limit=ignore_voter_limit
            )

            # print("REGISTER_RESULT", register_result)
            if register_result.is_ok():
                _, newly_registered = register_result.unwrap()
                if newly_registered:
                    return UserRegistrationStatus.REGISTERED
                else:
                    return UserRegistrationStatus.ALREADY_REGISTERED
            else:
                assert register_result.is_err()
                return register_result.err_value

    @staticmethod
    def check_has_voted(poll_id: int, user_id: UserID) -> bool:
        return PollVoters.select().where(
            (PollVoters.user == user_id) &
            (PollVoters.poll == poll_id) &
            (PollVoters.voted == True)
        ).exists()

    @classmethod
    def is_poll_voter(cls, poll_id: int, user_id: UserID) -> bool:
        return cls.get_poll_voter(poll_id=poll_id, user_id=user_id).is_ok()

    @classmethod
    def get_poll_message(
        cls, poll_id: int, user_id: UserID, bot_username: str,
        username: Optional[str]
    ) -> Result[PollMessage, MessageBuilder]:
        if not cls.has_access_to_poll_id(
            poll_id=poll_id, user_id=user_id, username=username
        ):
            return Err(MessageBuilder().add(
                f'You have no access to poll {poll_id}'
            ))

        return Ok(cls._get_poll_message(
            poll_id=poll_id, bot_username=bot_username
        ))

    @classmethod
    def _get_poll_message(
        cls, poll_id: int, bot_username: str
    ) -> PollMessage:
        poll_info = cls._read_poll_info(poll_id=poll_id)
        return cls._generate_poll_message(
            poll_info=poll_info, bot_username=bot_username
        )

    @classmethod
    def _generate_poll_message(
        cls, poll_info: PollInfo, bot_username: str,
    ) -> PollMessage:
        poll_metadata = poll_info.metadata
        poll_message = cls.generate_poll_info(
            poll_metadata.id, poll_metadata.question,
            poll_info.poll_options, closed=poll_metadata.closed,
            bot_username=bot_username,
            num_voters=poll_metadata.num_active_voters,
            num_votes=poll_metadata.num_votes
        )

        reply_markup = None
        if poll_metadata.open_registration:
            vote_markup_data = cls.build_group_vote_markup(
                poll_id=poll_metadata.id
            )
            reply_markup = InlineKeyboardMarkup(vote_markup_data)

        return PollMessage(
            text=poll_message, reply_markup=reply_markup
        )

    @classmethod
    def build_group_vote_markup(
        cls, poll_id: int
    ) -> List[List[InlineKeyboardButton]]:
        callback_data = json.dumps(cls.kwargify(
            poll_id=poll_id, command=str(CallbackCommands.REGISTER)
        ))
        markup_layout = [[InlineKeyboardButton(
            text=f'Register for poll', callback_data=callback_data
        )]]
        return markup_layout

    @classmethod
    def read_poll_info(
        cls, poll_id: int, user_id: UserID, username: Optional[str]
    ) -> Result[PollInfo, MessageBuilder]:
        error_message = MessageBuilder()
        has_poll_access = cls.has_access_to_poll_id(
            poll_id, user_id, username=username
        )
        if not has_poll_access:
            error_message.add(f'You have no access to poll {poll_id}')
            return Err(error_message)

        return Ok(cls._read_poll_info(poll_id=poll_id))

    @classmethod
    def _read_poll_info(cls, poll_id: int) -> PollInfo:
        poll_metadata = Polls.read_poll_metadata(poll_id)
        poll_option_rows = PollOptions.select().where(
            PollOptions.poll == poll_id
        ).order_by(PollOptions.option_number)

        poll_options = [
            poll_option.option_name for poll_option in poll_option_rows
        ]
        poll_option_rankings = [
            poll_option.option_number for poll_option in poll_option_rows
        ]

        return PollInfo(
            metadata=poll_metadata, poll_options=poll_options,
            option_numbers=poll_option_rankings
        )

    @classmethod
    def has_access_to_poll_id(
        cls, poll_id: int, user_id: UserID, username: Optional[str]
    ) -> bool:
        """
        returns whether the user is a member or creator of the poll
        """
        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            return False

        return cls.has_access_to_poll(
            poll, user_id=user_id, username=username
        )

    @classmethod
    def has_access_to_poll(
        cls, poll: Polls, user_id: UserID, username: Optional[str]
    ) -> bool:
        poll_id = poll.id
        creator_id = poll.creator.id
        assert isinstance(creator_id, int)

        if creator_id == user_id:
            return True
        if cls.is_poll_voter(poll_id, user_id):
            return True

        if username is not None:
            assert isinstance(username, str)
            whitelist_entry_result = cls.get_whitelist_entry(
                username=username, poll_id=poll_id, user_id=user_id
            )
            if whitelist_entry_result.is_ok():
                return True

        return False

    @staticmethod
    def resolve_username_to_user_tele_ids(username: str) -> List[int]:
        try:
            matching_users = Users.select().where(Users.username == username)
        except Users.DoesNotExist:
            return []

        user_tele_ids = [user.tele_id for user in matching_users]
        return user_tele_ids

    @staticmethod
    def get_whitelist_entry(
        poll_id: int, user_id: UserID, username: str
    ) -> Result[UsernameWhitelist, UserRegistrationStatus]:
        assert isinstance(poll_id, int)
        assert isinstance(user_id, int)
        assert isinstance(username, str)

        query = UsernameWhitelist.select().where(
            (UsernameWhitelist.username == username) &
            (UsernameWhitelist.poll == poll_id)
        )

        if not query.exists():
            # error_message = MessageBuilder()
            # error_message.add(f"You're not a voter of poll {poll_id}")
            return Err(UserRegistrationStatus.NOT_WHITELISTED)

        whitelist_entry: UsernameWhitelist = query.get()
        whitelisted_user: Users = whitelist_entry.user
        # increment number of registered voters
        whitelist_inapplicable = (
            (whitelisted_user is not None) and
            (whitelisted_user.id != user_id)
        )

        if whitelist_inapplicable:
            return Err(UserRegistrationStatus.USERNAME_TAKEN)

        return Ok(whitelist_entry)

    @staticmethod
    def generate_poll_info(
        poll_id, poll_question, poll_options: list[str],
        bot_username: str, num_votes: int = 0, num_voters: int = 0,
        closed: bool = False
    ):
        close_tag = '(closed)' if closed else ''
        numbered_poll_options = [
            f'{k + 1}. {poll_option}' for k, poll_option
            in enumerate(poll_options)
        ]

        args = f'poll_id={poll_id}'
        stamp = int(time.time())
        deep_link_url = (
            f'https://t.me/{bot_username}?start={args}&stamp={stamp}'
        )

        return (
            textwrap.dedent(f"""
            POLL ID: {poll_id} {close_tag}
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

    @classmethod
    def sign_data_check_string(
        cls, data_check_string: str
    ) -> str:
        bot_token = cls.__get_telegram_token()
        secret_key = hmac.new(
            key=b"WebAppData", msg=bot_token.encode(),
            digestmod=hashlib.sha256
        ).digest()

        validation_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()
        return validation_hash

    @classmethod
    def sign_message(cls, message: str) -> str:
        bot_token = cls.__get_telegram_token()
        secret_key = hmac.new(
            key=b"SIGN_MESSAGE", msg=bot_token.encode(),
            digestmod=hashlib.sha256
        ).digest()

        validation_hash = hmac.new(
            secret_key, message.encode(), hashlib.sha256
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

    @staticmethod
    def validate_rankings(
        rankings: List[int]
    ) -> Result[bool, MessageBuilder]:
        error_message = MessageBuilder()

        # print('rankings =', rankings)
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
        cls, poll_id: int, rankings: List[int], user_tele_id: int,
        username: Optional[str]
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
        :param user_tele_id: voter's telegram user tele id
        :param username: voter's telegram username
        :return:
        """
        error_message = MessageBuilder()
        if len(rankings) == 0:
            error_message.add('At least one ranking must be provided')
            return Err(error_message)

        validate_result = cls.validate_rankings(rankings)
        if validate_result.is_err():
            return validate_result

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            error_message.add(f'Poll {poll_id} does not exist')
            return Err(error_message)

        if poll.closed:
            error_message.add('Poll has already been closed')
            return Err(error_message)

        try:
            user = Users.build_from_fields(tele_id=user_tele_id).get()
        except Users.DoesNotExist:
            error_message.add(f'UNEXPECTED ERROR: USER DOES NOT EXIST')
            return Err(error_message)

        user_id = user.get_user_id()
        # verify that the user can vote for the poll
        # print('PRE_VERIFY', poll_id, user_id, username)
        verify_result = cls.verify_voter(poll_id, user_id, username)
        if verify_result.is_err():
            error_message.add(f"You're not a voter of poll {poll_id}")
            return Err(error_message)

        poll_voter_id: int = verify_result.unwrap()
        # print('POLL_VOTER_ID', poll_voter_id)
        vote_register_result = cls.__unsafe_register_vote(
            poll_id=poll_id, poll_voter_id=poll_voter_id,
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
        poll_option_rows = PollOptions.select().where(
            PollOptions.poll == poll_id
        ).order_by(PollOptions.option_number)

        # map poll option ranking numbers to option ids
        option_rank_to_ids: Dict[int, int] = {}
        for poll_option_row in poll_option_rows:
            option_no = poll_option_row.option_number
            option_rank_to_ids[option_no] = poll_option_row.id

        vote_rankings: List[Dict[str, Any]] = []

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
            vote_ranking_row = cls.kwargify(
                poll_voter_id=poll_voter_id,
                option_id=poll_option_id, special_value=special_vote_val,
                ranking=ranking
            )

            vote_rankings.append(vote_ranking_row)

        assert len(vote_rankings) > 0
        # clear previous vote by the same user on the same poll
        delete_vote_query = VoteRankings.delete().where(
            VoteRankings.poll_voter == poll_voter_id
        )

        with db.atomic():
            num_rows_deleted = delete_vote_query.execute()
            # whether the user cast a vote for this poll for the first time
            is_first_vote = num_rows_deleted == 0
            VoteRankings.insert_many(vote_rankings).execute()

            if is_first_vote:
                # declare that the voter has cast a vote
                PollVoters.update(voted=True).where(
                    PollVoters.id == poll_voter_id
                ).execute()
                # increment record for number of votes cast for poll
                Polls.update(num_votes=Polls.num_votes+1).where(
                    Polls.id == poll_id
                ).execute()

        return Ok(True)

    @staticmethod
    def kwargify(**kwargs):
        return kwargs
