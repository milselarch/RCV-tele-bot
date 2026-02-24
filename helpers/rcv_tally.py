import asyncio
import dataclasses

from concurrent.futures import ThreadPoolExecutor
from aioredlock import LockError
from result import Result, Err, Ok

from database import PollWinners, Polls, VoteRankings, PollVoters
from helpers.message_buillder import MessageBuilder
from helpers.redis_cache_manager import RedisCacheManager, GetPollWinnerStatus
from py_rcv import VotesCounter as PyVotesCounter, PyEliminationStrategies

"""
helpers to actually calculate / retrieve the winner of a 
ranked choice poll
"""


@dataclasses.dataclass
class GetPollWinnerInfo(object):
    poll: Polls
    poll_winner_id: int
    status: GetPollWinnerStatus


@dataclasses.dataclass
class DeterminePollWinnerInfo(object):
    winning_option_id: int
    poll: Polls


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
    def _determine_poll_winner(
        cls, poll_id: int
    ) -> Result[DeterminePollWinnerInfo, None]:
        """
        Runs the ranked choice voting algorithm to determine
        the winner of the poll
        :param poll_id:
        :return:
        ID of winning option, or None if there's no winner
        """
        poll = cls.fetch_poll(poll_id)
        if poll.is_err():
            return Err(None)

        poll = poll.unwrap()
        num_poll_voters = poll.num_active_voters
        vote_algorithm_no = poll.vote_algorithm
        vote_strategy = PyEliminationStrategies.from_int(vote_algorithm_no)
        # TODO: add a way for poll creator to specify the vote strategy

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
        votes_aggregator = PyVotesCounter(elimination_strategy=vote_strategy)

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

        return Ok(DeterminePollWinnerInfo(
            winning_option_id=winning_option_id, poll=poll
        ))

    async def get_poll_winner(
        self, poll_id: int
    ) -> Result[GetPollWinnerInfo, GetPollWinnerStatus]:
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
            poll_winner_id = cache_result.unwrap()
            fetch_poll_result = self.fetch_poll(poll_id)

            if fetch_poll_result.is_err():
                return Err(GetPollWinnerStatus.POLL_FETCH_FAILED)

            poll = fetch_poll_result.unwrap()
            # print('CACHE_HIT', cache_result)
            return Ok(GetPollWinnerInfo(
                poll=poll, poll_winner_id=poll_winner_id,
                status=GetPollWinnerStatus.CACHED
            ))

        redis_lock_key = self.cache.build_poll_winner_lock_cache_key(poll_id)
        # print('CACHE_KEY', redis_cache_key)
        if await self.cache.is_locked(redis_lock_key):
            # print('PRE_LOCKED')
            return Err(GetPollWinnerStatus.COMPUTING)
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
                        poll_winner_id = cache_result.unwrap()
                        fetch_poll_result = self.fetch_poll(poll_id)

                        if fetch_poll_result.is_err():
                            return Err(GetPollWinnerStatus.FAILED)

                        poll = fetch_poll_result.unwrap()
                        # print('INNER_CACHE_HIT', cache_result)
                        return Ok(GetPollWinnerInfo(
                            poll=poll, poll_winner_id=poll_winner_id,
                            status=GetPollWinnerStatus.CACHED
                        ))

                    # compute the winner in a separate thread to not block
                    # the async event loop
                    loop = asyncio.get_event_loop()
                    with ThreadPoolExecutor() as executor:
                        poll_winner_res = await loop.run_in_executor(
                            executor, self._determine_poll_winner, poll_id
                        )

                    if poll_winner_res.is_err():
                        return Err(GetPollWinnerStatus.FAILED)

                    poll_winner_info = poll_winner_res.unwrap()
                    poll_winner_id = poll_winner_info.winning_option_id
                    poll = poll_winner_info.poll

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
            return Err(GetPollWinnerStatus.COMPUTING)

        # print('CACHE_MISS', poll_winner_id)
        return Ok(GetPollWinnerInfo(
            poll_winner_id=poll_winner_id, poll=poll,
            status=GetPollWinnerStatus.NEWLY_COMPUTED
        ))
