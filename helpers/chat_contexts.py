import dataclasses
import textwrap

from enum import StrEnum
from typing import Sequence
from result import Result, Ok, Err
from telegram import Message

from helpers import helpers
from helpers.commands import Command
from helpers.constants import (
    POLL_MAX_OPTIONS, POLL_OPTION_MAX_LENGTH, BLANK_ID, MAX_POLL_QUESTION_LENGTH
)
from helpers.contexts import BaseVoteContext
from helpers.message_buillder import MessageBuilder
from helpers.strings import POLL_OPTIONS_LIMIT_REACHED_TEXT

from database.subscription_tiers import SubscriptionTiers
from database.db_helpers import BoundRowFields, UserID
from database import db, CallbackContextState
from database import (
    ChatContextStateTypes, SerializableChatContext, Users, Polls,
    ChatWhitelist, UsernameWhitelist, PollOptions, PollVoters
)
from tele_helpers import ModifiedTeleUpdate


@dataclasses.dataclass
class ExtractedChatContext(object):
    user: Users
    message_text: str
    chat_context: CallbackContextState
    context_type: ChatContextStateTypes


class ExtractChatContextErrors(StrEnum):
    NO_CHAT_CONTEXT = "NO_MESSAGE_CONTEXT"
    LOAD_FAILED = "LOAD_FAILED"

    def to_message(self):
        if self == ExtractChatContextErrors.NO_CHAT_CONTEXT:
            return (
                f"Use /{Command.HELP} to view all available commands, "
                f"/{Command.CREATE_GROUP_POLL} to create a new poll, "
                f"or /{Command.VOTE} to vote for an existing poll "
            )
        elif self == ExtractChatContextErrors.LOAD_FAILED:
            return "Unexpected error loading chat context type"
        else:
            return "CONTEXT_TYPE_UNKNOWN"


def extract_chat_context(
    update: ModifiedTeleUpdate
) -> Result[ExtractedChatContext, ExtractChatContextErrors]:
    message: Message = update.message
    user_entry: Users = update.user
    assert isinstance(message.text, str)
    assert len(message.text) > 0
    message_text: str = message.text

    chat_context_res = CallbackContextState.build_from_fields(
        user_id=user_entry.get_user_id(), chat_id=message.chat.id
    ).safe_get()

    if chat_context_res.is_err():
        return Err(ExtractChatContextErrors.NO_CHAT_CONTEXT)

    chat_context = chat_context_res.unwrap()
    chat_context_type_res = chat_context.get_context_type()
    if chat_context_type_res.is_err():
        chat_context.delete()
        return Err(ExtractChatContextErrors.LOAD_FAILED)

    chat_context_type = chat_context_type_res.unwrap()
    return Ok(ExtractedChatContext(
        user=user_entry, message_text=message_text,
        chat_context=chat_context, context_type=chat_context_type
    ))


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
        elif len(self.poll_question) > MAX_POLL_QUESTION_LENGTH:
            return Err(error_message.add(textwrap.dedent(f"""
                Poll question character limit is {MAX_POLL_QUESTION_LENGTH}
                Poll question is {len(self.poll_question)} characters long
            """)))

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

    def save_poll_to_db(self) -> Result[Polls, MessageBuilder]:
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
            # whitelist voters in the poll by username
            for raw_poll_user in self.whitelisted_usernames:
                row_fields = UsernameWhitelist.build_from_fields(
                    poll_id=new_poll_id, username=raw_poll_user
                )
                whitelist_user_rows.append(row_fields)
            # whitelist voters in the poll by user id
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

        return Ok(new_poll)


class PollCreationChatContext(SerializableChatContext):
    chat_id: int
    user_id: int
    poll_options: list[str]

    whitelisted_chat_ids: Sequence[int] = ()
    open_registration: bool = True
    question: str = ''

    def __init__(self, **kwargs):
        # TODO: type hint the input params somehow?
        super().__init__(**kwargs)

    def get_context_type(self) -> ChatContextStateTypes:
        return ChatContextStateTypes.POLL_CREATION

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
        elif len(question) > MAX_POLL_QUESTION_LENGTH:
            return Err(ValueError(
                f"Question cannot exceed {MAX_POLL_QUESTION_LENGTH} "
                f"characters"
            ))

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


class VoteChatContext(SerializableChatContext, BaseVoteContext):
    chat_id: int
    ref_message_id: int = BLANK_ID
    ref_chat_id: int = BLANK_ID

    def __init__(
        self, poll_id: int = BLANK_ID,
        ref_message_id: int = BLANK_ID,
        ref_chat_id: int = BLANK_ID,
        rankings: Sequence[int] = (), **kwargs
    ):
        # TODO: type hint the input params somehow?
        super().__init__(
            poll_id=poll_id, rankings=list(rankings),
            ref_message_id=ref_message_id, ref_chat_id=ref_chat_id,
            **kwargs
        )

    def get_user_id(self) -> UserID:
        return UserID(self.user_id)

    def get_chat_id(self) -> int:
        return self.chat_id

    def get_context_type(self) -> ChatContextStateTypes:
        return ChatContextStateTypes.VOTE


class EditPollTitleChatContext(SerializableChatContext):
    chat_id: int
    user_id: int
    poll_id: int
    new_title: str = ""

    def get_user_id(self) -> UserID:
        return UserID(self.user_id)

    def get_chat_id(self) -> int:
        return self.chat_id

    def get_context_type(self) -> ChatContextStateTypes:
        return ChatContextStateTypes.EDIT_POLL_TITLE


class PaySupportChatContext(SerializableChatContext):
    user_id: int
    chat_id: int

    def get_user_id(self) -> UserID:
        return UserID(self.user_id)

    def get_chat_id(self) -> int:
        return self.chat_id

    def get_context_type(self) -> ChatContextStateTypes:
        return ChatContextStateTypes.PAY_SUPPORT


class ClosePollChatContext(SerializableChatContext):
    chat_id: int
    user_id: int

    def get_user_id(self) -> UserID:
        return UserID(self.user_id)

    def get_chat_id(self) -> int:
        return self.chat_id

    def get_context_type(self) -> ChatContextStateTypes:
        return ChatContextStateTypes.CLOSE_POLL
