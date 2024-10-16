import unittest
# noinspection PyUnresolvedReferences
# import ParentImport
import ranked_choice_vote

from SpecialVotes import SpecialVotes


class TestRankedChoiceVote(unittest.TestCase):
    def __init__(self, *args, verbose=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.verbose = verbose

    """
    Unittests for the core ranked choice voting algorithm itself
    """
    def test_basic_scenario(self):
        # Basic test with predefined votes
        votes_aggregator = ranked_choice_vote.VotesAggregator()
        votes = [
            [1, 2, 3, 4],
            [1, 2, 3],
            [3],
            [3, 2, 4],
            [4, 1]
        ]

        for vote_idx in range(len(votes)):
            vote_rankings = votes[vote_idx]
            for vote_ranking in vote_rankings:
                votes_aggregator.insert_vote_ranking(vote_idx, vote_ranking)

        votes_aggregator.flush_votes()
        winner = votes_aggregator.determine_winner()
        self.assertEqual(
            winner, 1,
            "The winner should be candidate 1"
        )

    def test_simple_majority(self):
        # Basic test where there is a winner in round 1
        votes_aggregator = ranked_choice_vote.VotesAggregator()
        votes = [
            [1, 2, 3, 4],
            [1, 2, 3],
            [3],
            [3, 2, 4],
            [1, 2]
        ]

        for vote_idx in range(len(votes)):
            vote_rankings = votes[vote_idx]
            for vote_ranking in vote_rankings:
                votes_aggregator.insert_vote_ranking(vote_idx, vote_ranking)

        votes_aggregator.flush_votes()
        winner = votes_aggregator.determine_winner()
        self.assertEqual(
            winner, 1,
            "The winner should be candidate 1"
        )

    def test_tie_scenario(self):
        # Test for a tie
        votes_aggregator = ranked_choice_vote.VotesAggregator()
        votes = [
            [1, 2],
            [2, 1]
        ]
        for vote_idx in range(len(votes)):
            vote_rankings = votes[vote_idx]
            for vote_ranking in vote_rankings:
                votes_aggregator.insert_vote_ranking(vote_idx, vote_ranking)

        votes_aggregator.flush_votes()
        winner = votes_aggregator.determine_winner()
        self.assertIsNone(winner, "There should be a tie")

    def test_zero_vote_end(self):
        # Test that a zero vote ends with no one winning
        votes_aggregator = ranked_choice_vote.VotesAggregator()
        votes = [
            [1, SpecialVotes.WITHHOLD_VOTE],
            [2, 1],
            [3, 2],
            [3]
        ]
        for vote_idx in range(len(votes)):
            vote_rankings = votes[vote_idx]
            for vote_ranking in vote_rankings:
                votes_aggregator.insert_vote_ranking(vote_idx, vote_ranking)

        votes_aggregator.flush_votes()
        winner = votes_aggregator.determine_winner()
        self.assertEqual(
            winner, None,
            "Candidate 1's vote should not count, no one should win"
        )

    def test_zero_nil_votes_only(self):
        # Test that having only zero and nil votes ends with no one winning,
        # and also that there are no errors in computing the poll result
        votes_aggregator = ranked_choice_vote.VotesAggregator()
        votes = [
            [SpecialVotes.WITHHOLD_VOTE],
            [SpecialVotes.WITHHOLD_VOTE],
            [SpecialVotes.WITHHOLD_VOTE],
            [SpecialVotes.ABSTAIN_VOTE]
        ]
        for vote_idx in range(len(votes)):
            vote_rankings = votes[vote_idx]
            for vote_ranking in vote_rankings:
                votes_aggregator.insert_vote_ranking(vote_idx, vote_ranking)

        votes_aggregator.flush_votes()
        winner = votes_aggregator.determine_winner()
        self.assertEqual(
            winner, None,
            "No one should win if all votes were 0 or nil"
        )

    def test_null_vote_end(self):
        # Test that a null vote ends with someone winning
        votes_aggregator = ranked_choice_vote.VotesAggregator()
        votes = [
            [1, SpecialVotes.ABSTAIN_VOTE],
            [2, 1],
            [3, 2],
            [3]
        ]
        for vote_idx in range(len(votes)):
            vote_rankings = votes[vote_idx]
            for vote_ranking in vote_rankings:
                votes_aggregator.insert_vote_ranking(vote_idx, vote_ranking)

        votes_aggregator.flush_votes()
        winner = votes_aggregator.determine_winner()
        self.assertEqual(
            winner, 3,
            "Candidate 3's vote should not count, no one should win"
        )

    def test_majoritarian_rule(self):
        votes_aggregator = ranked_choice_vote.VotesAggregator()
        votes = [
            [1, 6, 15],
            [1, 2, 6, 15, 5, 4, 7, 3, 11],
            [6, 15, 1, 11, 10, 16, 17, 8, 2, 3, 5, 7],
            [9, 8, 6, 11, 13, 3, 1],
            [13, 14, 16, 6, 3, 4, 5, 2, 1, 8, 9]
        ]
        for vote_idx in range(len(votes)):
            vote_rankings = votes[vote_idx]
            for vote_ranking in vote_rankings:
                votes_aggregator.insert_vote_ranking(vote_idx, vote_ranking)

        votes_aggregator.flush_votes()
        winner = votes_aggregator.determine_winner()
        self.assertEqual(
            winner, 6,
            "Candidate 6 should be the majoritarian winner"
        )


if __name__ == '__main__':
    unittest.main()
