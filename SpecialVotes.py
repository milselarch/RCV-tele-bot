from enum import IntEnum
from typing import Dict


class SpecialVotes(IntEnum):
    # a withhold vote signals a commitment to not vote for any
    # of the candidates, and that the voter wants none of them to win
    WITHHOLD_VOTE = -1
    # an abstain vote signals a commitment to not vote for any
    # of the candidates, and that the voter is unsure who should win
    # but still wants there to be a winner in the end of the poll
    ABSTAIN_VOTE = -2

    __string_map__ = {WITHHOLD_VOTE: 'withhold', ABSTAIN_VOTE: 'abstain'}
    __inv_string_map__ = None

    @classmethod
    def get_string_map(cls) -> Dict:
        return getattr(cls, '__string_map__')

    @classmethod
    def get_str_values(cls):
        return tuple(cls.get_string_map().values())

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

        if str_value in inv_map:
            return cls(inv_map[str_value])
        else:
            raise ValueError(f'BAD ENUM VALUE: {str_value}')

    def to_string(self) -> str:
        string_map = self.get_string_map()
        return string_map[self]

    @staticmethod
    def is_valid(int_value):
        try:
            SpecialVotes(int_value)
        except ValueError:
            return False

        return True
