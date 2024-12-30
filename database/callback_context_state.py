from __future__ import annotations

import json
import datetime
import pydantic

from database.users import Users
from database.db_helpers import UserID, EmptyField, Empty, BoundRowFields
from database.setup import database_proxy, BaseModel

from enum import StrEnum
from typing import TypeVar, Type, Self
from abc import ABCMeta, abstractmethod
from result import Result, Ok, Err
from helpers import constants
from peewee import (
    BigAutoField, ForeignKeyField, BigIntegerField, CharField,
    TextField, DateTimeField
)

P = TypeVar('P', bound=pydantic.BaseModel)


class ChatContextStateTypes(StrEnum):
    POLL_CREATION = "POLL_CREATION"
    INCREASE_MAX_VOTERS = "INCREASE_MAX_VOTERS"
    PAY_SUPPORT = "PAY_SUPPORT"
    CLOSE_POLL = "CLOSE_POLL"
    VOTE = "VOTE"


class CallbackContextState(BaseModel):
    id = BigAutoField(primary_key=True)
    user = ForeignKeyField(Users, to_field='id', on_delete='CASCADE')
    chat_id = BigIntegerField(null=False)  # telegram chat ID
    context_type = CharField(max_length=255, null=False)
    state = TextField(null=False)
    last_updated_at = DateTimeField(default=datetime.datetime.now, null=False)

    indexes = (
        # Unique multi-column index for user-chat_id pairs
        (('user', 'chat_id'), True),
    )

    def update_state(self, new_state: SerializableChatContext):
        self.state = new_state.dump_to_json_str()
        self.last_updated_at = datetime.datetime.now()
        self.save()

    def get_context_type(self) -> Result[ChatContextStateTypes, ValueError]:
        try:
            return Ok(ChatContextStateTypes(self.context_type))
        except ValueError as e:
            return Err(e)

    def deserialize_state(self) -> dict[str, any]:
        return json.loads(self.state)

    @classmethod
    def prune_expired_contexts(cls):
        # remove expired contexts from the database
        date_stamp = datetime.datetime.now()
        deletion_cutoff = date_stamp - constants.DELETE_CONTEXTS_BACKLOG
        # noinspection PyTypeChecker
        cls.delete().where(
            cls.last_updated_at < deletion_cutoff
        ).execute()

    @classmethod
    def build_from_fields(
        cls, user_id: UserID | EmptyField = Empty,
        chat_id: int | EmptyField = Empty,
        context_type: ChatContextStateTypes | EmptyField = Empty,
        state: SerializableChatContext | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        raw_context_type: str | EmptyField = Empty
        if context_type is not Empty:
            raw_context_type = str(context_type)

        serialized_state: str | EmptyField = Empty
        if state is not Empty:
            serialized_state = state.dump_to_json_str()

        return BoundRowFields(cls, {
            cls.user: user_id, cls.chat_id: chat_id,
            cls.context_type: raw_context_type,
            cls.state: serialized_state
        })


class SerializableChatContext(pydantic.BaseModel, metaclass=ABCMeta):
    def dump_to_json_str(self) -> str:
        return json.dumps(self.model_dump(mode='json'))

    @abstractmethod
    def get_user_id(self) -> UserID:
        raise NotImplementedError

    @abstractmethod
    def get_chat_id(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_context_type(self) -> ChatContextStateTypes:
        raise NotImplementedError

    def save_state(self) -> CallbackContextState:
        user_id = self.get_user_id()
        chat_id = self.get_chat_id()
        context_type = self.get_context_type()

        with database_proxy.atomic():
            # delete other chat contexts in the same chat
            CallbackContextState.delete().where(
                (CallbackContextState.user == user_id) &
                (CallbackContextState.chat_id == chat_id) &
                (CallbackContextState.context_type != context_type)
            ).execute()
            # get existing chat context and update it
            context_state, _ = CallbackContextState.build_from_fields(
                user_id=user_id, chat_id=chat_id,
                context_type=context_type
            ).get_or_create()

            context_state.update_state(self)
            return context_state

    def delete_context(
        self, user_id: UserID, chat_id: int
    ) -> bool:
        with database_proxy.atomic():
            context_state_res = CallbackContextState.build_from_fields(
                user_id=user_id, chat_id=chat_id,
                context_type=self.get_context_type()
            ).safe_get()

            if context_state_res.is_err():
                return False

            context_state = context_state_res.unwrap()
            context_state.delete_instance()
            return True

    @classmethod
    def load(
        cls: Type[P], context: CallbackContextState
    ) -> Result[P, ValueError]:
        try:
            model: P = cls.model_validate_json(context.state)
            return Ok(model)
        except ValueError as e:
            Err(e)
