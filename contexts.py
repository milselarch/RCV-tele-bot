import pydantic

from result import Result, Ok, Err
from PyVotesCounter import PyVotesCounter


class PollCreationContext(pydantic.BaseModel):
    question: str
    poll_options: list[str]

    def __init__(self, question: str, max_options: int):
        super().__init__(question=question, options=[])
        self.max_options = max_options

    @property
    def is_complete(self):
        return len(self.poll_options) > 0

    def add_option(self, option: str) -> Result[bool, ValueError]:
        if option in self.poll_options:
            return Err(ValueError(f"Option {option} already exists"))
        if len(self.poll_options) == self.max_options:
            return Err(ValueError("Max number of options reached"))

        self.poll_options.append(option)
        return Ok(self.is_complete)


class VoteContext(pydantic.BaseModel):
    poll_id: int
    rankings: list[int]

    def __init__(self, poll_id: int, max_rankings: int):
        super().__init__(poll_id=poll_id, rankings=[])
        self.max_rankings = max_rankings

    @property
    def is_complete(self):
        return len(self.rankings) > 0

    def add_option(self, raw_option_id: int) -> Result[bool, ValueError]:
        if len(self.rankings) == self.max_rankings:
            return Err(ValueError("Max number of rankings reached"))

        new_rankings = self.rankings + [raw_option_id]
        valid, error_message = PyVotesCounter.validate_raw_vote(new_rankings)
        if not valid:
            return Err(ValueError(error_message))

        self.rankings.append(raw_option_id)
        return Ok(self.is_complete)
