from typing import Optional, Sequence
from ranked_choice_vote import VotesAggregator


class PyVotesCounter(object):
    """
    A wrapper around ranked_choice_vote.VotesAggregator
    to get around type hinting not working for generated files
    """
    def __init__(self):
        self.votes_counter: VotesAggregator = VotesAggregator()

    def flush_votes(self) -> bool:
        return self.votes_counter.flush_votes()

    def get_num_votes(self) -> int:
        return self.votes_counter.get_num_votes()

    def insert_vote_ranking(self, vote_id: int, vote_ranking: int) -> None:
        return self.votes_counter.insert_vote_ranking(vote_id, vote_ranking)

    def insert_empty_votes(self, num_votes: int) -> bool:
        return self.votes_counter.insert_empty_votes(num_votes)

    def determine_winner(self) -> Optional[int]:
        return self.votes_counter.determine_winner()

    @staticmethod
    def validate_raw_vote(rankings: Sequence[int]) -> (bool, str):
        return VotesAggregator.validate_raw_vote(rankings)
