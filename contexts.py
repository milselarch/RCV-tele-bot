import pydantic


class PollCreationContext(pydantic.BaseModel):
    question: str
    poll_options: list[str]

    def __init__(self, question: str):
        super().__init__(question=question, options=[])


class VoteContext(pydantic.BaseModel):
    poll_id: int
    option_ids: list[int]

    def __init__(self, poll_id: int):
        super().__init__(poll_id=poll_id, option_ids=[])
