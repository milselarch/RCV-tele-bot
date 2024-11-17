import dataclasses
from enum import StrEnum

from typing import Sequence
from result import Err, Ok, Result
from telegram import Message

from database import Users
from database.db_helpers import UserID
from database.message_context_state import (
    SerializableMessageContext, MessageContextStateTypes, MessageContextState
)
from helpers.constants import BLANK_POLL_ID
from helpers.contexts import BaseVoteContext
from tele_helpers import ModifiedTeleUpdate


@dataclasses.dataclass
class ExtractedMessageContext(object):
    user: Users
    message_context: MessageContextState
    context_type: MessageContextStateTypes


class ExtractMessageContextErrors(StrEnum):
    NO_MESSAGE_CONTEXT = "NO_MESSAGE_CONTEXT"
    LOAD_FAILED = "LOAD_FAILED"


def extract_message_context(
    update: ModifiedTeleUpdate
) -> Result[ExtractedMessageContext, ExtractMessageContextErrors]:
    query = update.callback_query
    message_id = query.message.message_id
    user_entry: Users = update.user

    message_context_res = MessageContextState.build_from_fields(
        user_id=user_entry.get_user_id(), message_id=message_id
    ).safe_get()

    if message_context_res.is_err():
        return Err(ExtractMessageContextErrors.NO_MESSAGE_CONTEXT)

    message_context = message_context_res.unwrap()
    message_context_type_res = message_context.get_context_type()
    if message_context_type_res.is_err():
        message_context.delete()
        return Err(ExtractMessageContextErrors.LOAD_FAILED)

    message_context_type = message_context_type_res.unwrap()
    return Ok(ExtractedMessageContext(
        user=user_entry, message_context=message_context,
        context_type=message_context_type
    ))


class VoteMessageContext(SerializableMessageContext, BaseVoteContext):
    message_id: int

    def __init__(
        self, poll_id: int, rankings: Sequence[int] = (), **kwargs
    ):
        # TODO: type hint the input params somehow?
        super().__init__(poll_id=poll_id, rankings=list(rankings), **kwargs)

    def get_user_id(self) -> UserID:
        return UserID(self.user_id)

    def get_message_id(self) -> int:
        return self.message_id

    def get_context_type(self) -> MessageContextStateTypes:
        return MessageContextStateTypes.VOTE
