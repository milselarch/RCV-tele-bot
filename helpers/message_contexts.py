from database.db_helpers import UserID
from database.message_context_state import (
    SerializableMessageContext, MessageContextStateTypes
)
from helpers.contexts import GenericVoteContext


class VoteMessageContext(SerializableMessageContext, GenericVoteContext):
    def get_user_id(self) -> UserID:
        return UserID(self.user_id)

    def get_message_id(self) -> int:
        return self.message_id

    def get_context_type(self) -> MessageContextStateTypes:
        return MessageContextStateTypes.VOTE
