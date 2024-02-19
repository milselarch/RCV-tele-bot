from typing import List, Tuple, Optional
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

        self.index: int = 0
        self.choices: List[int] = []

        for choice in choices:
            assert isinstance(choice, int)
            self.add_next_choice(choice)

    def __repr__(self):
        return self.__class__.__name__ + f'({self.choices})'

    def __len__(self):
        return len(self.choices)

    def raw_choices(self) -> Tuple[int, ...]:
        return tuple(self.choices)

    def is_preferred_over(self, option_1, option_2) -> bool:
        # whether option_1 is preferred over option_2
        has_option_1 = option_1 in self.choices
        has_option_2 = option_2 in self.choices

        if not has_option_1 and not has_option_2:
            return False
        elif not has_option_2:
            return True
        elif not has_option_1:
            return False

        assert has_option_1 and has_option_2
        return self.choices.index(option_1) < self.choices.index(option_2)

    def get_inverse_score(self, option: int) -> float:
        if option in self.choices:
            return 1 / (self.choices.index(option) + 1)
        else:
            return 0

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
        return self.top_choice

    def has_next_choice(self):
        return self.index < len(self.choices) - 1

    @property
    def last_choice(self):
        return self.get_last_choice()

    def get_last_choice(self) -> Optional[int]:
        if len(self.choices) == 0:
            return None

        return self.choices[-1]

    @property
    def top_choice(self):
        return self.get_top_choice()

    def get_top_choice(self) -> Optional[int]:
        if len(self.choices) == 0:
            return None

        return self.choices[self.index]