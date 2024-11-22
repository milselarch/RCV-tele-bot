from __future__ import annotations

import datetime
import pydantic
import json

from enum import StrEnum
from abc import ABCMeta, abstractmethod
from result import Result, Ok, Err
from typing import Self, Type, TypeVar

from database.db_helpers import UserID, BoundRowFields, EmptyField, Empty
from database.users import Users
from database.setup import BaseModel, database_proxy
from peewee import (
    ForeignKeyField, BigAutoField, BigIntegerField, CharField,
    TextField, DateTimeField
)

from helpers import constants

P = TypeVar('P', bound=pydantic.BaseModel)


class MessageContextStateTypes(StrEnum):
    VOTE = 'VOTE'


class MessageContextState(BaseModel):
    id = BigAutoField(primary_key=True)
    user = ForeignKeyField(Users, to_field='id', on_delete='CASCADE')
    message_id = BigIntegerField(null=False)
    context_type = CharField(max_length=255, null=False)
    state = TextField(null=False)
    last_updated_at = DateTimeField(default=datetime.datetime.now, null=False)

    indexes = (
        # Unique multi-column index for user-message_id pairs
        (('user', 'message_id'), True),
    )

    def update_state(self, new_state: SerializableMessageContext):
        self.state = new_state.dump_to_json_str()
        self.last_updated_at = datetime.datetime.now()
        self.save()

    def get_context_type(self) -> Result[
        MessageContextStateTypes, ValueError
    ]:
        try:
            return Ok(MessageContextStateTypes(self.context_type))
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
        message_id: int | EmptyField = Empty,
        context_type: MessageContextStateTypes | EmptyField = Empty,
        state: SerializableMessageContext | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        raw_context_type: str | EmptyField = Empty
        if context_type is not Empty:
            raw_context_type = str(context_type)

        serialized_state: str | EmptyField = Empty
        if state is not Empty:
            serialized_state = state.dump_to_json_str()

        return BoundRowFields(cls, {
            cls.user: user_id, cls.message_id: message_id,
            cls.context_type: raw_context_type,
            cls.state: serialized_state
        })


class SerializableMessageContext(pydantic.BaseModel, metaclass=ABCMeta):
    def dump_to_json_str(self) -> str:
        return json.dumps(self.model_dump(mode='json'))

    @abstractmethod
    def get_user_id(self) -> UserID:
        raise NotImplementedError

    @abstractmethod
    def get_message_id(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_context_type(self) -> MessageContextStateTypes:
        raise NotImplementedError

    def save_state(self) -> MessageContextState:
        user_id = self.get_user_id()
        message_id = self.get_message_id()

        with database_proxy.atomic():
            context_state, _ = MessageContextState.build_from_fields(
                user_id=user_id, message_id=message_id,
                context_type=self.get_context_type()
            ).get_or_create()

            context_state.update_state(self)
            return context_state

    def delete_context(
        self, user_id: UserID, message_id: int
    ) -> bool:
        with database_proxy.atomic():
            context_state_res = MessageContextState.build_from_fields(
                user_id=user_id, message_id=message_id,
                context_type=self.get_context_type()
            ).safe_get()

            if context_state_res.is_err():
                return False

            context_state = context_state_res.unwrap()
            context_state.delete_instance()
            return True

    @classmethod
    def load(
        cls: Type[P], context: MessageContextState
    ) -> Result[P, ValueError]:
        try:
            model: P = cls.model_validate_json(context.state)
            return Ok(model)
        except ValueError as e:
            return Err(e)
