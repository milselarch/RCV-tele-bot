import unittest

from helpers.special_votes import SpecialVotes
from py_rcv import VotesCounter as PyVotesCounter


class TestRankedChoiceVote(unittest.TestCase):
    def __init__(self, *args, verbose=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.verbose = verbose

    """
    Unittests for the core ranked choice voting algorithm itself
    """
    def test_basic_scenario(self):
        # Basic test with predefined votes
        votes_aggregator = PyVotesCounter()
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
        votes_aggregator = PyVotesCounter()
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
        votes_aggregator = PyVotesCounter()
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
        votes_aggregator = PyVotesCounter()
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
        votes_aggregator = PyVotesCounter()
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
        votes_aggregator = PyVotesCounter()
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
        votes_aggregator = PyVotesCounter()
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


class TestVoteValidation(unittest.TestCase):
    def __init__(self, *args, verbose=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.verbose = verbose

    def test_valid_votes(self):
        votes_aggregator = PyVotesCounter()
        valid_votes = [
            [1, 2, 7, 3], [1, 2, 3], [4], [-2], [-1], [1, 0, 3, 5],
            [2, 5, 1, -1], [2, 5, 1, 11, -2]
        ]

        for valid_vote in valid_votes:
            validate_result = votes_aggregator.validate_raw_vote(valid_vote)
            assert isinstance(validate_result.error_message, str)
            assert isinstance(validate_result.valid, bool)

            is_valid = validate_result.valid
            self.assertTrue(is_valid, "Vote should be valid")
            self.assertTrue(
                len(validate_result.error_message) == 0,
                "Error message should be empty"
            )

    def test_duplicate_rankings(self):
        votes_aggregator = PyVotesCounter()
        validate_result = votes_aggregator.validate_raw_vote([1, 2, 2])
        assert isinstance(validate_result.error_message, str)
        assert isinstance(validate_result.valid, bool)
        # print('validate_result', validate_result)

        is_valid = validate_result.valid
        self.assertFalse(is_valid, "non-unique vote is invalid")
        self.assertTrue(
            len(validate_result.error_message) > 0,
            "Error message should be non-empty"
        )

    def test_non_final_special_vote(self):
        votes_aggregator = PyVotesCounter()
        validate_result = votes_aggregator.validate_raw_vote([1, 2, -1, 2])
        assert isinstance(validate_result.error_message, str)
        assert isinstance(validate_result.valid, bool)
        # print('validate_result', validate_result)

        is_valid = validate_result.valid
        self.assertFalse(is_valid, "vote with non-final special vote is invalid")
        self.assertTrue(
            len(validate_result.error_message) > 0,
            "Error message should be non-empty"
        )


if __name__ == '__main__':
    unittest.main()
