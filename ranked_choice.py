import copy
import itertools

from typing import List, Tuple, Dict
from enum import Enum, IntEnum, unique


class SpecialVoteValues(IntEnum):
    ZERO_VOTE = -1
    NULL_VOTE = -2

    __string_map__ = {ZERO_VOTE: '0', NULL_VOTE: 'nil'}
    __inv_string_map__ = None

    @classmethod
    def get_string_map(cls) -> Dict:
        return getattr(cls, '__string_map__')

    @classmethod
    def get_inv_map(cls):
        if cls.__inv_string_map__ is not None:
            return cls.__inv_string_map__

        inv_map = {}
        string_map = cls.get_string_map()

        for enum_val in string_map:
            string_val = string_map[enum_val]
            inv_map[string_val] = cls(enum_val)

        cls.__inv_string_map__ = inv_map
        return inv_map

    @classmethod
    def from_string(cls, str_value: str):
        inv_map = cls.get_inv_map()
        return cls(inv_map[str_value])

    def to_string(self) -> str:
        string_map = self.get_string_map()
        return string_map[self]


def ranked_choice_vote(
    ranked_votes: List[List[int]], num_voters: int = None
):
    """
    :param num_voters:
    :param ranked_votes:
    a list of ranked votes
    each ranked vote is a list of candidate preferences
    first choice is rightmost (highest index) element in list
    last choice is leftmost (lowest index) element in list

    special vote ranking values:
    ZERO_VOTE (0) - give the vote to none of the options in the poll
    NULL_VOTE (None) - remove the voter from the poll
    :return:
    """
    ranked_votes = copy.deepcopy(ranked_votes)

    if num_voters is None:
        num_voters = len(ranked_votes)

    # number of voters who have not voided their votes
    effective_num_voters = num_voters
    assert num_voters >= len(ranked_votes)
    unique_candidates = set(itertools.chain(*ranked_votes))

    # remove 0 and None votes from unique candidates
    if SpecialVoteValues.ZERO_VOTE in unique_candidates:
        unique_candidates.remove(SpecialVoteValues.ZERO_VOTE)
    if SpecialVoteValues.NULL_VOTE in unique_candidates:
        unique_candidates.remove(SpecialVoteValues.NULL_VOTE)

    candidate_votes_map = {
        candidate: 0 for candidate in unique_candidates
    }

    winner, rounds = None, 0
    # count how many votes each candidate got
    # using the first-choice votes of each voter
    for ranked_vote in ranked_votes:
        top_choice = ranked_vote[-1]

        if top_choice == SpecialVoteValues.ZERO_VOTE:
            # 0 means voter has chosen to vote for no one
            pass
        elif top_choice == SpecialVoteValues.NULL_VOTE:
            # None means the voter has chosen to remove
            # himself from the poll
            effective_num_voters -= 1
        else:
            assert top_choice in unique_candidates
            candidate_votes_map[top_choice] += 1

    while winner is None:
        winner = None
        rounds += 1

        print(f'ROUND {rounds}')
        print('ranked-votes', ranked_votes)
        print('vote-map', candidate_votes_map)

        candidate_vote_counts = list(candidate_votes_map.values())
        candidate_vote_counts = [
            votes for votes in candidate_vote_counts if votes > 0
        ]

        lowest_votes = min(candidate_vote_counts)
        highest_votes = max(candidate_vote_counts)

        if len(candidate_vote_counts) == 2:
            # if there are two 1st choice candidates have the exact
            # same number of votes and there are no other 1st choice
            # candidates, then we declare the vote to be tied
            if lowest_votes == highest_votes:
                return None

        weakest_candidates = []
        # see if any candidate has won the vote
        for candidate in candidate_votes_map:
            candidate_votes = candidate_votes_map[candidate]
            if candidate_votes == lowest_votes:
                weakest_candidates.append(candidate)

            if candidate_votes > effective_num_voters / 2:
                winner = candidate
                break

        print('dropping candidates', weakest_candidates)
        if winner is not None:
            break

        vote_transfers = 0
        # vote transfer to next choice for worst performing candidate(s)
        for ranked_vote in ranked_votes:
            # print('RANKED-VOTE', ranked_vote, vote_transfers)
            if len(ranked_vote) == 1:
                # voter has no 2nd choice preference
                # so cannot do vote transfer
                # print('SKIP_VOTE')
                continue

            # get active top choice candidate
            # for the current ranked choice vote
            top_choice = ranked_vote[-1]
            assert top_choice in candidate_votes_map

            if top_choice in weakest_candidates:
                # remove the top candidate from the current
                # ranked choice vote if aforementioned candidate
                # is the weakest candidate
                ranked_vote.pop()
                # get next choice that the voter wants
                next_choice = ranked_vote[-1]

                if next_choice == SpecialVoteValues.ZERO_VOTE:
                    candidate_votes_map[top_choice] -= 1
                elif next_choice == SpecialVoteValues.NULL_VOTE:
                    candidate_votes_map[top_choice] -= 1
                    effective_num_voters -= 1
                else:
                    candidate_votes_map[top_choice] -= 1
                    candidate_votes_map[next_choice] += 1

                vote_transfers += 1

        if vote_transfers == 0:
            # no voter had their candidate preference shifted
            # so there isn't a winner overall
            # print('0 VOTE TRANSFERS')
            break

    print(f'winner = {winner}')
    return winner


if __name__ == '__main__':
    print(ranked_choice_vote([
        [4, 3, 2, 1],
        [3, 2, 4],
        [3, 1],
        [4, 2, 3]
    ]))
