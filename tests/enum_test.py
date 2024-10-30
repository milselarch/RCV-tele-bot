import unittest
# noinspection PyUnresolvedReferences
# import ParentImport

from special_votes import SpecialVotes as SpecV


class TestSpecialVotes(unittest.TestCase):
    """
    Unittests for SpecialVotes IntEnum implementation
    """
    def test_enum_values(self):
        # Test the enum values are correct
        self.assertEqual(SpecV.WITHHOLD_VOTE, -1)
        self.assertEqual(SpecV.ABSTAIN_VOTE, -2)

    def test_to_string(self):
        # Test the to_string method
        self.assertEqual(SpecV.WITHHOLD_VOTE.to_string(), 'withhold')
        self.assertEqual(SpecV.ABSTAIN_VOTE.to_string(), 'abstain')

    def test_from_string(self):
        # Test the from_string method
        self.assertEqual(SpecV.from_string('withhold'), SpecV.WITHHOLD_VOTE)
        self.assertEqual(SpecV.from_string('abstain'), SpecV.ABSTAIN_VOTE)
        with self.assertRaises(ValueError):
            SpecV.from_string('invalid')

    def test_is_valid(self):
        # Test the is_valid static method
        self.assertTrue(SpecV.is_valid(-1))
        self.assertTrue(SpecV.is_valid(-2))
        self.assertFalse(SpecV.is_valid(999))  # Some invalid value

    def test_string_maps(self):
        # Test if string maps are correctly set
        self.assertEqual(
            SpecV.get_string_map(), {
                SpecV.WITHHOLD_VOTE: 'withhold', SpecV.ABSTAIN_VOTE: 'abstain'
            }
        )


if __name__ == '__main__':
    unittest.main()

