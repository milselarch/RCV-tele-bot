import pydantic

from abc import ABCMeta
from typing import Sequence
from result import Result, Err, Ok

from database.db_helpers import UserID
from helpers import strings
from helpers.commands import Command
from helpers.constants import POLL_MAX_OPTIONS, BLANK_POLL_ID
from helpers.special_votes import SpecialVotes
from py_rcv import VotesCounter as PyVotesCounter


class GenericVoteContext(pydantic.BaseModel, metaclass=ABCMeta):
    chat_id: int
    user_id: int

    poll_id: int
    rankings: list[int]
    max_options: int = POLL_MAX_OPTIONS

    def __init__(
        self, poll_id: int = BLANK_POLL_ID,
        rankings: Sequence[int] = (), **kwargs
    ):
        # TODO: type hint the input params somehow?
        super().__init__(poll_id=poll_id, rankings=list(rankings), **kwargs)

    def get_user_id(self) -> UserID:
        return UserID(self.user_id)

    def get_chat_id(self) -> int:
        return self.chat_id

    def set_poll_id_from_str(
        self, raw_poll_id: str
    ) -> Result[bool, ValueError]:
        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            return Err(ValueError("Invalid poll ID"))

        return self.set_poll_id(poll_id)

    def set_poll_id(self, poll_id: int) -> Result[bool, ValueError]:
        if poll_id < 0:
            return Err(ValueError("Invalid poll ID"))

        self.poll_id = poll_id
        return Ok(self.is_complete)

    def set_max_options(self, max_options: int):
        self.max_options = max_options

    @property
    def has_poll_id(self):
        return self.poll_id >= 0

    @property
    def is_complete(self):
        return (len(self.rankings) > 0) and self.has_poll_id

    @property
    def num_options(self) -> int:
        return len(self.rankings)

    def to_vote_message(self) -> str:
        return f'{self.poll_id}: ' + ' > '.join([
            str(raw_option) if raw_option > 0 else
            SpecialVotes(raw_option).to_string()
            for raw_option in self.rankings
        ])

    def generate_vote_option_prompt(self) -> str:
        if len(self.rankings) == 0:
            return strings.generate_vote_option_prompt(1)
        else:
            return (
                self.to_vote_message() + '\n' +
                strings.generate_vote_option_prompt(self.num_options+1)
            )

    def add_option(self, raw_option_number: int) -> Result[bool, ValueError]:
        special_vote_res = SpecialVotes.try_from(raw_option_number)
        is_valid = (
            special_vote_res.is_ok() or
            (self.max_options >= raw_option_number >= 1)
        )
        if not is_valid:
            return Err(ValueError(
                f"Please enter an option from 1 to {self.max_options}, "
                f"or enter abstain or withhold, or "
                f"/{Command.DONE} if you're done"
            ))

        new_rankings = self.rankings + [raw_option_number]
        validate_result = PyVotesCounter.validate_raw_vote(new_rankings)
        if not validate_result.valid:
            return Err(ValueError(validate_result.error_message))

        self.rankings.append(raw_option_number)
        return Ok(self.is_complete)
