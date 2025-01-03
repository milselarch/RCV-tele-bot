import asyncio

from concurrent.futures import ThreadPoolExecutor
from typing import Tuple, Optional
from aioredlock import LockError
from result import Result, Err, Ok

from database import PollWinners, Polls, VoteRankings, PollVoters
from helpers.message_buillder import MessageBuilder
from helpers.redis_cache_manager import RedisCacheManager, GetPollWinnerStatus
from py_rcv import VotesCounter as PyVotesCounter


class RCVTally(object):
    def __init__(self):
        self.cache = RedisCacheManager()

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

        redis_lock_key = self.cache.build_poll_winner_lock_cache_key(poll_id)
        # print('CACHE_KEY', redis_cache_key)
        if await self.cache.is_locked(redis_lock_key):
            # print('PRE_LOCKED')
            return None, GetPollWinnerStatus.COMPUTING

        try:
            # prevents race conditions where multiple computations
            # are run concurrently for the same poll
            async with await self.cache.lock(redis_lock_key) as lock:
                # Start a task to refresh the lock periodically
                refresh_task = asyncio.create_task(
                    self.cache.refresh_lock(lock)
                )

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