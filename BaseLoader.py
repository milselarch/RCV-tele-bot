import time
import textwrap
import dataclasses
from typing import List

from database import *
from result import Ok, Err, Result
from MessageBuilder import MessageBuilder


@dataclasses.dataclass
class PollInfo(object):
    poll_id: int
    poll_question: str
    poll_options: List[str]
    num_poll_voters: int
    num_poll_votes: int


class BaseLoader(object):
    @staticmethod
    def get_poll_voter(poll_id, chat_username):
        # check if voter is part of the poll
        voter = PollVoters.select().join(
            Polls, on=(Polls.id == PollVoters.poll_id)
        ).where(
            (Polls.id == poll_id) &
            (PollVoters.username == chat_username)
        )

        return voter

    @classmethod
    def is_poll_voter(cls, *args, **kwargs):
        return cls.get_poll_voter(*args, **kwargs).count() > 0

    @classmethod
    def _view_poll(
        cls, poll_id: int, chat_username: str, bot_username: str
    ) -> Result[str, MessageBuilder]:
        read_poll_result = cls.read_poll_info(
            poll_id=poll_id, chat_username=chat_username
        )

        if read_poll_result.is_err():
            return read_poll_result

        poll_info = read_poll_result.ok()
        poll_message = cls.generate_poll_info(
            poll_info.poll_id, poll_info.poll_question,
            poll_info.poll_options,
            bot_username=bot_username,
            num_voters=poll_info.num_poll_voters,
            num_votes=poll_info.num_poll_votes
        )

        return Ok(poll_message)

    @classmethod
    def read_poll_info(
        cls, poll_id: int, chat_username: str
    ) -> Result[PollInfo, MessageBuilder]:
        error_message = MessageBuilder()

        poll = Polls.select().where(Polls.id == poll_id).get()
        has_poll_access = cls.has_poll_access(poll_id, chat_username)
        if not has_poll_access:
            error_message.add(f'You have no access to poll {poll_id}')
            return Err(error_message)

        poll_option_rows = Options.select().where(
            Options.poll_id == poll.id
        ).order_by(Options.option_number)

        poll_options = [
            poll_option.option_name for poll_option in poll_option_rows
        ]

        poll_question = poll.desc
        num_poll_voters = PollVoters.select().where(
            PollVoters.poll_id == poll_id
        ).count()
        # count number of first choice votes in poll
        num_poll_votes = Votes.select().where(
            (Votes.poll_id == poll_id) &
            (Votes.ranking == 0)
        ).count()

        return Ok(PollInfo(
            poll_id=poll_id, poll_question=poll_question,
            poll_options=poll_options, num_poll_voters=num_poll_voters,
            num_poll_votes=num_poll_votes
        ))

    @classmethod
    def has_poll_access(cls, poll_id, chat_username):
        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            return False

        voter_in_poll = cls.is_poll_voter(poll_id, chat_username)
        return voter_in_poll or (poll.creator == chat_username)

    @staticmethod
    def extract_poll_id(update) -> Result[int, MessageBuilder]:
        message = update.message
        raw_text = message.text.strip()
        error_message = MessageBuilder()

        if ' ' not in raw_text:
            error_message.add('no poll id specified')
            return Err(error_message)

        raw_poll_id = raw_text[raw_text.index(' '):].strip()

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            error_message.add(f'invalid poll id: {raw_poll_id}')
            return Err(error_message)

        return Ok(poll_id)

    @staticmethod
    def generate_poll_info(
            poll_id, poll_question, poll_options, bot_username,
            num_votes=0, num_voters=0
    ):
        numbered_poll_options = [
            f'{k + 1}. {poll_option}' for k, poll_option,
            in enumerate(poll_options)
        ]

        args = f'poll_id={poll_id}'
        stamp = int(time.time())
        deep_link_url = (
            f'https://t.me/{bot_username}?start={args}&stamp={stamp}'
        )

        return (
                textwrap.dedent(f"""
                POLL ID: {poll_id}
                POLL QUESTION: 
                {poll_question}
                ——————————————————
                {num_votes} / {num_voters} voted
                ——————————————————
            """) + f'\n'.join(numbered_poll_options) +
                f'\n——————————————————'
                f'\nvote on the webapp at {deep_link_url}'
        )
