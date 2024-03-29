import unittest
import ParentImport

from RankedChoice import ranked_choice_vote
from RankedVote import RankedVote
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
        votes = [
            RankedVote([1, 2, 3, 4]),
            RankedVote([1, 2, 3]),
            RankedVote([3]),
            RankedVote([3, 2, 4]),
            RankedVote([4, 1])
        ]
        result = ranked_choice_vote(votes, verbose=self.verbose)
        self.assertEqual(
            result, 1,
            "The winner should be candidate 1"
        )

    def test_simple_majority(self):
        # Basic test where there is a winner in round 1
        votes = [
            RankedVote([1, 2, 3, 4]),
            RankedVote([1, 2, 3]),
            RankedVote([3]),
            RankedVote([3, 2, 4]),
            RankedVote([1, 2])
        ]
        result = ranked_choice_vote(votes, verbose=self.verbose)
        self.assertEqual(
            result, 1,
            "The winner should be candidate 1"
        )

    def test_tie_scenario(self):
        # Test for a tie
        votes = [
            RankedVote([1, 2]),
            RankedVote([2, 1])
        ]
        result = ranked_choice_vote(votes)
        self.assertIsNone(result, "There should be a tie")

    def test_zero_vote_end(self):
        # Test that a zero vote ends with no one winning
        votes = [
            RankedVote([1, SpecialVotes.WITHHOLD_VOTE]),
            RankedVote([2, 1]),
            RankedVote([3, 2]),
            RankedVote([3])
        ]
        result = ranked_choice_vote(votes, verbose=self.verbose)
        self.assertEqual(
            result, None,
            "Candidate 1's vote should not count, no one should win"
        )

    def test_zero_nil_votes_only(self):
        # Test that having only zero and nil votes ends with no one winning,
        # and also that there are no errors in computing the poll result
        votes = [
            RankedVote([SpecialVotes.WITHHOLD_VOTE]),
            RankedVote([SpecialVotes.WITHHOLD_VOTE]),
            RankedVote([SpecialVotes.WITHHOLD_VOTE]),
            RankedVote([SpecialVotes.ABSTAIN_VOTE])
        ]
        result = ranked_choice_vote(votes, verbose=self.verbose)
        self.assertEqual(
            result, None,
            "No one should win if all votes were 0 or nil"
        )

    def test_null_vote_end(self):
        # Test that a null vote ends with someone winning
        votes = [
            RankedVote([1, SpecialVotes.ABSTAIN_VOTE]),
            RankedVote([2, 1]),
            RankedVote([3, 2]),
            RankedVote([3])
        ]
        result = ranked_choice_vote(votes, verbose=self.verbose)
        self.assertEqual(
            result, 3,
            "Candidate 3's vote should not count, no one should win"
        )

    def test_majoritarian_rule(self):
        votes = [
            RankedVote([1, 6, 15]),
            RankedVote([1, 2, 6, 15, 5, 4, 7, 3, 11]),
            RankedVote([6, 15, 1, 11, 10, 16, 17, 8, 2, 3, 5, 7]),
            RankedVote([9, 8, 6, 11, 13, 3, 1]),
            RankedVote([13, 14, 16, 6, 3, 4, 5, 2, 1, 8, 9])
        ]

        result = ranked_choice_vote(votes, verbose=self.verbose)
        self.assertEqual(
            result, 6,
            "Candidate 6 should be the majoritarian winner"
        )


if __name__ == '__main__':
    unittest.main()
