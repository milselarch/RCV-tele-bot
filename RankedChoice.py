import copy

from collections import defaultdict, deque
from SpecialVotes import SpecialVotes
from RankedVote import RankedVote
from typing import List, Dict, Optional, Set, Tuple


class PreferenceGraph(object):
    # TODO: implement preferences update when option is removed
    # TODO: implement preferences update upon ABSTAIN vote cast
    # TODO: implement preferences update upon WITHDRAW vote cast
    def __init__(
        self, candidates: List[int] = None,
        ranked_votes: List[RankedVote] = None
    ):
        self.preferences_over: Dict[int, Set[int]] = {}
        self.preferences_under: Dict[int, Set[int]] = {}
        self.preference_count: Dict[Tuple[int, int], int] = {}

        self.candidates = candidates
        self.ranked_votes = ranked_votes
        self.num_votes = len(self.ranked_votes)
        self.built = False
        self.build()

    def build(self):
        assert not self.built
        pairs = zip(self.candidates, self.candidates)

        for pair in pairs:
            candidate, other_candidate = pair
            if candidate == other_candidate:
                continue

            preferred_count = 0
            for vote in self.ranked_votes:
                if vote.is_preferred_over(candidate, other_candidate):
                    preferred_count += 1

            self.preference_count[pair] = preferred_count

            if preferred_count > self.num_votes // 2:
                if candidate not in self.preferences_over:
                    self.preferences_over[candidate] = set()
                if candidate not in self.preferences_under:
                    self.preferences_under[candidate] = set()

                self.preferences_over[candidate].add(other_candidate)
                self.preferences_under[other_candidate].add(candidate)

        self.built = True

    def get_strong_weak_candidates(self) -> Tuple[List[int], List[int]]:
        # strongest_candidates are candidates where there
        # are no other candidates that are preferred over it
        # weakest_candidates ares candidates where there
        # are no other candidates that are preferred under it
        strongest_candidates, weakest_candidates = [], []

        for candidate in self.candidates:
            is_strongest = len(self.preferences_over[candidate]) == 0
            is_weakest = len(self.preferences_under[candidate]) == 0

            if is_strongest and is_weakest:
                # candidate is not in pecking order at all
                # pecking order is impossible to establish
                return [], []

            if is_strongest:
                strongest_candidates.append(candidate)
            elif is_weakest:
                weakest_candidates.append(candidate)

        return strongest_candidates, weakest_candidates


def resolve_weakest_candidates(
    candidates: List[int], ranked_votes: List[RankedVote]
) -> Optional[List[int]]:
    if len(candidates) <= 1:
        return candidates

    assert len(candidates) == len(set(candidates))

    # strongest_candidates are candidates where there
    # are no other candidates that are preferred over it
    # weakest_candidates ares candidates where there
    # are no other candidates that are preferred under it
    graph = PreferenceGraph(
        candidates=candidates, ranked_votes=ranked_votes
    )

    strong_weak_candidates = graph.get_strong_weak_candidates()
    strongest_candidates, weakest_candidates = strong_weak_candidates

    if (len(strongest_candidates) == 0) or (len(weakest_candidates) == 0):
        # pecking order contains a cycle
        return None

    # TODO: check for a cycle in the pecking order graph
    explored = set()
    queue = deque([(None, str)])


def ranked_choice_vote(
    ranked_votes: List[RankedVote], num_voters: int = None,
    verbose: bool = False
):
    """
    :param num_voters:
    total number of voters in the poll
    :param verbose:
    prints intermediate results, diagnostic information if True
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
    log = print if verbose else lambda *args, **kwargs: None
    ranked_votes = copy.deepcopy(ranked_votes)

    if num_voters is None:
        num_voters = len(ranked_votes)

    # number of voters who have not voided their votes
    effective_num_voters = num_voters
    assert num_voters >= len(ranked_votes)

    unique_candidates = set()
    for ranked_vote in ranked_votes:
        ranked_vote_choices = ranked_vote.raw_choices()
        for candidate in ranked_vote_choices:
            unique_candidates.add(candidate)

    # remove 0 and None votes from unique candidates
    if SpecialVotes.ZERO_VOTE in unique_candidates:
        unique_candidates.remove(SpecialVotes.ZERO_VOTE)
    if SpecialVotes.NULL_VOTE in unique_candidates:
        unique_candidates.remove(SpecialVotes.NULL_VOTE)

    candidate_votes_map = {
        candidate: 0 for candidate in unique_candidates
    }

    winner, rounds = None, 0
    # count how many votes each candidate got
    # using the first-choice votes of each voter
    for ranked_vote in ranked_votes:
        top_choice = ranked_vote.top_choice

        if top_choice == SpecialVotes.ZERO_VOTE:
            # 0 means voter has chosen to vote for no one
            pass
        elif top_choice == SpecialVotes.NULL_VOTE:
            # None means the voter has chosen to remove
            # himself from the poll
            effective_num_voters -= 1
        else:
            assert top_choice in unique_candidates
            candidate_votes_map[top_choice] += 1

    while winner is None:
        winner = None
        rounds += 1

        log(f'ROUND {rounds}')
        log('ranked-votes', ranked_votes)
        log('vote-map', candidate_votes_map)

        candidate_vote_counts = list(candidate_votes_map.values())
        candidate_vote_counts = [
            votes for votes in candidate_vote_counts if votes > 0
        ]

        if len(candidate_vote_counts) == 0:
            lowest_votes, highest_votes = 0, 0
        else:
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

        log('dropping candidates', weakest_candidates)
        if winner is not None:
            break

        vote_transfers = 0
        # vote transfer to next choice for worst performing candidate(s)
        for ranked_vote in ranked_votes:
            # print('RANKED-VOTE', ranked_vote, vote_transfers)
            if not ranked_vote.has_next_choice():
                # voter has no next choice preference
                # so cannot do vote transfer
                # print('SKIP_VOTE')
                continue

            # get active top choice candidate
            # for the current ranked choice vote
            top_choice = ranked_vote.top_choice
            assert top_choice in candidate_votes_map

            if top_choice in weakest_candidates:
                # remove the top candidate from the current
                # ranked choice vote if aforementioned candidate
                # is the weakest candidate
                ranked_vote.transfer_to_next_choice()
                # get next choice that the voter wants
                next_choice = ranked_vote.top_choice

                if next_choice == SpecialVotes.ZERO_VOTE:
                    candidate_votes_map[top_choice] -= 1
                elif next_choice == SpecialVotes.NULL_VOTE:
                    candidate_votes_map[top_choice] -= 1
                    effective_num_voters -= 1
                else:
                    candidate_votes_map[top_choice] -= 1
                    candidate_votes_map[next_choice] += 1

                vote_transfers += 1

        if vote_transfers == 0:
            # no voter had their candidate preference shifted
            # so there isn't a winner overall
            log('0 VOTE TRANSFERS')
            break

    log(f'winner = {winner}')
    return winner


if __name__ == '__main__':
    poll_result = ranked_choice_vote([
        RankedVote([1, 2, 3, 4]),
        RankedVote([1, 2, 3]),
        RankedVote([3]),
        RankedVote([3, 2, 4]),
        RankedVote([4, 1])
    ])

    if poll_result == 1:
        print(f"Test passed: Winner is {poll_result}")
    else:
        print(f"Test failed: Expected 1, but got {poll_result}")
