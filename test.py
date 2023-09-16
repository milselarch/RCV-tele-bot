import copy
import itertools

from typing import List, Tuple


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
    :return:
    """
    ranked_votes = copy.deepcopy(ranked_votes)

    if num_voters is None:
        num_voters = len(ranked_votes)

    assert len(ranked_votes) >= num_voters
    unique_candidates = set(itertools.chain(*ranked_votes))
    candidate_votes_map = {
        candidate: 0 for candidate in unique_candidates
    }

    winner, rounds = None, 0
    # count how many votes each candidate got
    # using the first-choice votes of each voter
    for ranked_vote in ranked_votes:
        candidate_votes_map[ranked_vote[-1]] += 1

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
            # candidates, then we scan the number of candidate
            # occurrences in the 2nd, 3rd choices to tiebreak
            if lowest_votes == highest_votes:
                # TODO: implement this
                pass

        weakest_candidates = []
        # see if any candidate has won the vote
        for candidate in candidate_votes_map:
            candidate_votes = candidate_votes_map[candidate]
            if candidate_votes == lowest_votes:
                weakest_candidates.append(candidate)

            if candidate_votes > num_voters / 2:
                winner = candidate
                break

        print('dropping candidates', weakest_candidates)

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

            if top_choice in weakest_candidates:
                # remove the top candidate from the current
                # ranked choice vote if aforementioned candidate
                # is the weakest candidate
                ranked_vote.pop()

                next_choice = ranked_vote[-1]
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
