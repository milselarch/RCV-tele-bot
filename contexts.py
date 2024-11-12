import dataclasses
import textwrap

from typing import Sequence
from result import Result, Ok, Err

from helpers import helpers, strings
from helpers.constants import (
    BLANK_POLL_ID, POLL_MAX_OPTIONS, POLL_OPTION_MAX_LENGTH
)
from helpers.message_buillder import MessageBuilder
from helpers.special_votes import SpecialVotes
from helpers.strings import POLL_OPTIONS_LIMIT_REACHED_TEXT
from database.subscription_tiers import SubscriptionTiers
from database.db_helpers import BoundRowFields
from py_rcv import VotesCounter as PyVotesCounter
from database import db
from database.database import (
    ContextStates, SerializableBaseModel, UserID, Users, Polls,
    ChatWhitelist, UsernameWhitelist, PollOptions, PollVoters
)


@dataclasses.dataclass
class PollCreatorTemplate(object):
    creator_id: UserID
    user_rows: Sequence[BoundRowFields[Users]] = ()
    poll_user_tele_ids: Sequence[int] = ()
    subscription_tier: SubscriptionTiers = SubscriptionTiers.FREE
    poll_options: Sequence[str] = ()
    whitelisted_chat_ids: Sequence[int] = ()
    whitelisted_usernames: Sequence[str] = ()
    open_registration: bool = False
    poll_question: str = ''

    @property
    def initial_num_voters(self):
        return (
            len(self.poll_user_tele_ids) + len(self.whitelisted_usernames)
        )

    def validate_params(self) -> Result[None, MessageBuilder]:
        error_message = MessageBuilder()
        if self.poll_question == '':
            return Err(error_message.add('Poll question cannot be empty'))

        if len(self.poll_options) > POLL_MAX_OPTIONS:
            return Err(error_message.add(textwrap.dedent(f"""
                Poll can have at most {POLL_MAX_OPTIONS} options
                {len(self.poll_options)} poll options entered
            """)))
        elif len(self.poll_options) < 2:
            return Err(error_message.add(textwrap.dedent(f"""
                Poll must have at least 2 options
                {len(self.poll_options)} poll options entered
            """)))

        max_option_length = max([len(option) for option in self.poll_options])
        if max_option_length > POLL_OPTION_MAX_LENGTH:
            return Err(error_message.add(textwrap.dedent(f"""
                Poll option character limit is {POLL_OPTION_MAX_LENGTH}
                Longest option entered is {max_option_length} characters long
            """)))

        duplicate_tele_ids = helpers.get_duplicate_nums(
            self.poll_user_tele_ids
        )
        if len(duplicate_tele_ids) > 0:
            return Err(error_message.add(
                f'Duplicate user ids found: {duplicate_tele_ids}'
            ))

        assert len(set(duplicate_tele_ids)) == len(duplicate_tele_ids)
        num_user_created_polls = Polls.count_polls_created(self.creator_id)
        poll_creation_limit = self.subscription_tier.get_max_polls()
        max_voters = self.subscription_tier.get_max_voters()

        if num_user_created_polls >= poll_creation_limit:
            return Err(error_message.add(POLL_OPTIONS_LIMIT_REACHED_TEXT))
        if self.initial_num_voters > max_voters:
            return Err(error_message.add(f'Whitelisted voters exceeds limit'))

        assert self.initial_num_voters <= max_voters
        return Ok(None)

    def save_poll_to_db(self) -> Result[int, MessageBuilder]:
        validate_res = self.validate_params()
        if validate_res.is_err():
            return validate_res

        error_message = MessageBuilder()
        poll_creation_limit = self.subscription_tier.get_max_polls()

        with db.atomic():
            Users.batch_insert(self.user_rows).on_conflict_ignore().execute()
            query = Users.tele_id.in_(self.poll_user_tele_ids)
            users = Users.select().where(query)
            poll_user_ids = [user.get_user_id() for user in users]

            assert len(poll_user_ids) == len(self.poll_user_tele_ids)
            num_user_created_polls = Polls.count_polls_created(
                self.creator_id
            )
            # verify again that the number of polls created is still
            # within the limit to prevent race conditions
            if num_user_created_polls >= poll_creation_limit:
                return Err(error_message.add(POLL_OPTIONS_LIMIT_REACHED_TEXT))

            new_poll = Polls.build_from_fields(
                desc=self.poll_question, creator_id=self.creator_id,
                num_voters=self.initial_num_voters,
                open_registration=self.open_registration,
                max_voters=self.subscription_tier.get_max_voters()
            ).create()

            new_poll_id: int = new_poll.id
            assert isinstance(new_poll_id, int)
            chat_whitelist_rows: list[BoundRowFields[ChatWhitelist]] = []
            whitelist_user_rows: list[BoundRowFields[UsernameWhitelist]] = []
            poll_option_rows: list[BoundRowFields[PollOptions]] = []
            poll_voter_rows: list[BoundRowFields[PollVoters]] = []

            # create poll options
            for k, poll_option in enumerate(self.poll_options):
                poll_choice_number = k + 1
                poll_option_rows.append(PollOptions.build_from_fields(
                    poll_id=new_poll_id, option_name=poll_option,
                    option_number=poll_choice_number
                ))
            # whitelist voters in poll by username
            for raw_poll_user in self.whitelisted_usernames:
                row_fields = UsernameWhitelist.build_from_fields(
                    poll_id=new_poll_id, username=raw_poll_user
                )
                whitelist_user_rows.append(row_fields)
            # whitelist voters in poll by user id
            for poll_user_id in poll_user_ids:
                poll_voter_rows.append(PollVoters.build_from_fields(
                    poll_id=new_poll_id, user_id=poll_user_id
                ))
            # chat ids that are whitelisted for user self-registration
            for chat_id in self.whitelisted_chat_ids:
                chat_whitelist_rows.append(ChatWhitelist.build_from_fields(
                    poll_id=new_poll_id, chat_id=chat_id
                ))

            PollVoters.batch_insert(poll_voter_rows).execute()
            UsernameWhitelist.batch_insert(whitelist_user_rows).execute()
            PollOptions.batch_insert(poll_option_rows).execute()
            ChatWhitelist.batch_insert(chat_whitelist_rows).execute()

        return Ok(new_poll_id)


class PollCreationContext(SerializableBaseModel):
    chat_id: int
    user_id: int
    poll_options: list[str]

    whitelisted_chat_ids: Sequence[int] = ()
    open_registration: bool = False
    question: str = ''

    def __init__(self, **kwargs):
        # TODO: type hint the input params somehow?
        super().__init__(**kwargs)

    def get_context_type(self) -> ContextStates:
        return ContextStates.POLL_CREATION

    def get_user_id(self) -> UserID:
        return UserID(self.user_id)

    def get_chat_id(self) -> int:
        return self.chat_id

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
        if len(self.poll_options) >= POLL_MAX_OPTIONS:
            return Err(ValueError("Max number of options reached"))

        self.poll_options.append(option)
        return Ok(self.is_complete)

    def to_template(
        self, creator_id: UserID, subscription_tier: SubscriptionTiers
    ) -> PollCreatorTemplate:
        return PollCreatorTemplate(
            creator_id=creator_id,
            subscription_tier=subscription_tier,
            poll_options=self.poll_options,
            whitelisted_chat_ids=self.whitelisted_chat_ids,
            open_registration=self.open_registration,
            poll_question=self.question
        )


class VoteContext(SerializableBaseModel):
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

    def get_context_type(self) -> ContextStates:
        return ContextStates.CAST_VOTE

    def set_poll_id_from_str(self, raw_poll_id: str) -> Result[bool, ValueError]:
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
        # print('MAX_OPTIONS', self.max_options)
        if not (self.max_options >= raw_option_number >= 1):
            return Err(ValueError(
                f"Please enter an option from 1 to {self.max_options}, "
                f"or enter /abstain or /withhold"
            ))

        new_rankings = self.rankings + [raw_option_number]
        validate_result = PyVotesCounter.validate_raw_vote(new_rankings)
        if not validate_result.valid:
            return Err(ValueError(validate_result.error_message))

        self.rankings.append(raw_option_number)
        return Ok(self.is_complete)
