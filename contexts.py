from typing import Sequence
from result import Result, Ok, Err
from helpers.votes_counter import PyVotesCounter
from database.database import ContextStates, SerializableBaseModel


class PollCreationContext(SerializableBaseModel):
    question: str
    poll_options: list[str]
    whitelisted_chat_ids: Sequence[int]
    open_registration: bool

    def __init__(
        self, max_options: int, question: str = '',
        whitelisted_chat_ids: Sequence[int] = (),
        open_registration: bool = False
    ):
        super().__init__(
            question=question, options=[],
            whitelisted_chat_ids=whitelisted_chat_ids,
            open_registration=open_registration
        )
        self.max_options = max_options

    def get_context_type(self) -> ContextStates:
        return ContextStates.POLL_CREATION

    @property
    def has_question(self):
        return self.question.strip() != ''

    @property
    def num_poll_options(self) -> int:
        return len(self.poll_options)

    @property
    def is_complete(self):
        return (
            (len(self.poll_options) > 0) and
            (len(self.question) > 0)
        )

    def set_question(self, question: str) -> Result[bool, Exception]:
        question = question.strip()
        if len(question) == 0:
            return Err(ValueError("Question cannot be empty"))

        self.question = question
        return Ok(self.is_complete)

    def add_option(self, option: str) -> Result[bool, ValueError]:
        if option in self.poll_options:
            return Err(ValueError(f"Option {option} already exists"))
        if len(self.poll_options) == self.max_options:
            return Err(ValueError("Max number of options reached"))

        self.poll_options.append(option)
        return Ok(self.is_complete)


class VoteContext(SerializableBaseModel):
    poll_id: int
    rankings: list[int]

    def __init__(self, max_rankings: int, poll_id: int = -1):
        super().__init__(poll_id=poll_id, rankings=[])
        self.max_rankings = max_rankings

    def get_context_type(self) -> ContextStates:
        return ContextStates.CAST_VOTE

    def set_poll_id(self, poll_id: int) -> Result[bool, ValueError]:
        if poll_id < 0:
            return Err(ValueError("Invalid poll ID"))

        self.poll_id = poll_id
        return Ok(self.is_complete)

    @property
    def is_complete(self):
        return (
            (len(self.rankings) > 0) and
            (self.poll_id >= 0)
        )

    def add_option(self, raw_option_id: int) -> Result[bool, ValueError]:
        if len(self.rankings) == self.max_rankings:
            return Err(ValueError("Max number of rankings reached"))

        new_rankings = self.rankings + [raw_option_id]
        valid, error_message = PyVotesCounter.validate_raw_vote(new_rankings)
        if not valid:
            return Err(ValueError(error_message))

        self.rankings.append(raw_option_id)
        return Ok(self.is_complete)


