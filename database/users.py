from __future__ import annotations

import datetime

# noinspection PyUnresolvedReferences
from playhouse.shortcuts import ReconnectMixin
from result import Result, Ok, Err

from database import database
from database.setup import database_proxy
from helpers import constants
from .subscription_tiers import SubscriptionTiers
from typing import Self, List
from database.db_helpers import (
    BoundRowFields, Empty, EmptyField, UserID
)
from peewee import (
    BigIntegerField, CharField,
    IntegerField,  DateTimeField, BigAutoField
)

from database.setup import BaseModel


# maps telegram user ids to their usernames
class Users(BaseModel):
    id = BigAutoField(primary_key=True)
    # telegram user id
    tele_id = BigIntegerField(null=False, index=True, unique=True)
    username = CharField(max_length=255, default=None, null=True)
    credits = IntegerField(default=0)
    subscription_tier = IntegerField(default=0)
    deleted_at = DateTimeField(default=None, null=True)

    class Meta:
        database = database_proxy
        indexes = (
            # Non-unique index for usernames
            # telegram usernames have to be unique, however because
            # every username changes can't be tracked instantly
            # it possible there will be collisions here regardless
            (('username',), False),
        )

    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def get_subscription_tier(self) -> Result[SubscriptionTiers, ValueError]:
        try:
            return Ok(SubscriptionTiers(self.subscription_tier))
        except ValueError as e:
            return Err(e)

    @classmethod
    def prune_deleted_users(cls):
        date_stamp = datetime.datetime.now()
        user_deletion_cutoff = date_stamp - constants.DELETE_USERS_BACKLOG
        # actually remove deleted users from the database
        cls.delete().where(
            cls.deleted_at < user_deletion_cutoff
        ).execute()

    @classmethod
    def build_from_fields(
        cls, user_id: int | EmptyField = Empty,
        tele_id: int | EmptyField = Empty,
        username: str | None | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.id: user_id, cls.tele_id: tele_id, cls.username: username
        })

    def get_user_id(self) -> UserID:
        # TODO: do a unit test for this
        assert isinstance(self.id, int)
        return UserID(self.id)

    def get_tele_id(self) -> int:
        assert isinstance(self.tele_id, int)
        return self.tele_id

    @classmethod
    def get_from_tele_id(
        cls, tele_id: int
    ) -> Result[Users, Users.DoesNotExist]:
        return cls.build_from_fields(tele_id=tele_id).safe_get()

    def get_owned_polls(self) -> List[database.Polls]:
        return database.Polls.get_owned_polls(self.get_user_id())
