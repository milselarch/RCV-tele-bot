from typing import List
from SpecialVotes import SpecialVotes


class RankedVote(object):
    def __init__(
        self, choices: List[int] = None,
        first_to_last: bool = True
    ):
        if choices is None:
            choices = []
        if not first_to_last:
            choices = choices[::-1]

        self.index = 0
        self.choices = []

        for choice in choices:
            self.add_next_choice(choice)

    def __repr__(self):
        return self.__class__.__name__ + f'({self.choices})'

    def __len__(self):
        return len(self.choices)

    def raw_choices(self):
        return tuple(self.choices)

    def add_next_choice(self, choice: int):
        if isinstance(choice, SpecialVotes):
            choice = choice.value

        if type(choice) is not int:
            raise ValueError(f'INVALID VOTE TYPE: {choice}')

        assert (
            (choice > 0) or
            SpecialVotes.is_valid(choice)
        )

        if len(self.choices) > 0:
            # make sure last vote added is not a special vote
            last_vote_is_special = SpecialVotes.is_valid(self.last_choice)
            assert not last_vote_is_special

        assert choice not in self.choices
        self.choices.append(choice)

    def transfer_to_next_choice(self):
        assert self.has_next_choice()
        self.index += 1

    def has_next_choice(self):
        return self.index < len(self.choices) - 1

    @property
    def last_choice(self):
        return self.get_last_choice()

    def get_last_choice(self):
        if len(self.choices) == 0:
            return None

        return self.choices[-1]

    @property
    def top_choice(self):
        return self.get_top_choice()

    def get_top_choice(self):
        if len(self.choices) == 0:
            return None

        return self.choices[self.index]