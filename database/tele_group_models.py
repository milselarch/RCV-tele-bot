from typing import Self

from peewee import (
    AutoField, BigIntegerField, BooleanField, ForeignKeyField
)

from database.setup import BaseModel
from database.users import Users
from database.db_helpers import Empty, EmptyField, BoundRowFields


class TelegramGroup(BaseModel):
    id = AutoField(primary_key=True)
    tele_group_id = BigIntegerField(unique=True, null=False)
    allow_forwarding = BooleanField(default=True, null=False)

    @classmethod
    def build_from_fields(
        cls, group_id: int | EmptyField = Empty,
        tele_group_id: int | EmptyField = Empty,
        allow_forwarding: bool | EmptyField = Empty,
    ) -> BoundRowFields[Self]:
        return BoundRowFields[Self](cls, {
            cls.id: group_id,
            cls.tele_group_id: tele_group_id,
            cls.allow_forwarding: allow_forwarding,
        })


class TelegramGroupMembership(BaseModel):
    # TODO: auto track if a user is part of a group
    id = AutoField(primary_key=True)

    tele_group = ForeignKeyField(
        TelegramGroup,
        to_field="tele_group_id",
        on_delete="CASCADE",
        backref="memberships",
    )
    # FK to Users.tele_id
    tele_user = ForeignKeyField(
        Users,
        to_field="tele_id",
        column_name="tele_user_id",
        on_delete="CASCADE",
        backref="group_memberships",
    )

    class Meta:
        indexes = (
            # make sure a user can only be in a group once
            (("tele_group", "tele_user_id"), True),
        )

    @classmethod
    def build_from_fields(
        cls,
        membership_id: int | EmptyField = Empty,
        tele_group_id: int | EmptyField = Empty,
        tele_user_id: int | EmptyField = Empty,
    ) -> BoundRowFields[Self]:
        return BoundRowFields[Self](cls, {
            cls.id: membership_id,
            cls.tele_group: tele_group_id,
            cls.tele_user_id: tele_user_id,
        })
