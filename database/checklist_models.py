from __future__ import annotations

from typing import Self, List

import peewee
from result import Result, Ok, Err
from peewee import (
    AutoField, ForeignKeyField, CharField,
    IntegerField, DateTimeField, TextField, BooleanField
)

from database.db_helpers import UserID, Empty, EmptyField, BoundRowFields
from database.users import Users
from database.setup import BaseModel

from datetime import datetime


class Checklist(BaseModel):
    id = AutoField(primary_key=True)
    owner = ForeignKeyField(Users, to_field='id', on_delete='CASCADE')
    name = CharField(max_length=255)

    class Meta:
        # make sure every checklist has an indexable name
        # and that under a given owner, checklist names are unique
        indexes = (
            (("owner", "name"), True),
        )

    @classmethod
    def count_checklists_created(cls, user_id: UserID) -> int:
        return cls.select().where(cls.owner == user_id).count()

    @classmethod
    def build_from_fields(
        cls, checklist_id: int | EmptyField = Empty,
        owner_id: UserID | EmptyField = Empty,
        name: str | EmptyField = Empty
    ):
        return BoundRowFields[Self](cls, {
            cls.id: checklist_id,
            cls.owner: owner_id,
            cls.name: name
        })

    @classmethod
    def get_owned_checklists(cls, user_id: UserID) -> List[Checklist]:
        return [
            checklist for checklist in
            cls.select().where(cls.owner == user_id)
        ]

    @classmethod
    def get_as_owner(
        cls, checklist_id: int, owner_id: UserID
    ) -> Result[Checklist, peewee.DoesNotExist]:
        try:
            checklist = cls.get(
                (cls.id == checklist_id) & (cls.owner == owner_id)
            )
            return Ok(checklist)
        except cls.DoesNotExist as e:
            return Err(e)

    def get_items(self) -> List[ChecklistItem]:
        return ChecklistItem.get_all_items_for(int(self.id))


class ChecklistItem(BaseModel):
    id = AutoField(primary_key=True)
    checklist = ForeignKeyField(Checklist, to_field='id', on_delete='CASCADE')
    parent_item = ForeignKeyField(
        'self', to_field='id', null=True, on_delete='CASCADE'
    )
    name = CharField(max_length=255)
    checked = BooleanField(default=False)

    ordering = IntegerField(null=False)
    last_checked = DateTimeField(default=None)
    last_unchecked = DateTimeField(default=None)
    last_reminder = DateTimeField(default=None)

    class Meta:
        # make sure every checklist item has a unique ordering
        # within items with the same (checklist, parent item)
        indexes = (
            (("checklist", "parent_item", "ordering"), True),
        )

    @classmethod
    def build_from_fields(
        cls, item_id: int | EmptyField = Empty,
        checklist_id: int | EmptyField = Empty,
        parent_item_id: int | EmptyField = Empty,
        name: str | EmptyField = Empty,
        ordering: int | EmptyField = Empty,
        last_checked: datetime | EmptyField = Empty,
        last_unchecked: datetime | EmptyField = Empty,
        last_reminder: datetime | EmptyField = Empty
    ):
        return BoundRowFields[Self](cls, {
            cls.id: item_id,
            cls.checklist: checklist_id,
            cls.parent_item: parent_item_id,
            cls.name: name,
            cls.ordering: ordering,
            cls.last_checked: last_checked,
            cls.last_unchecked: last_unchecked,
            cls.last_reminder: last_reminder
        })

    @classmethod
    def get_all_items_for(cls, checklist_id: int) -> List[ChecklistItem]:
        query = cls.select().where(
            cls.checklist == checklist_id
        ).order_by(cls.ordering)
        return [
            checklist for checklist in query
        ]


class ChecklistItemActions(BaseModel):
    id = AutoField(primary_key=True)
    checklist_item = ForeignKeyField(
        ChecklistItem, to_field='id', on_delete='CASCADE'
    )
    ordering = IntegerField()
    # This will be JSON data that gets loaded onto a dataclass
    action = TextField(null=False)

    class Meta:
        # make sure every action has a unique ordering
        # within actions with the same parent checklist_item
        indexes = (
            (("checklist_item", "ordering"), True),
        )

    @classmethod
    def build_from_fields(
        cls, action_id: int | EmptyField = Empty,
        checklist_item_id: int | EmptyField = Empty,
        ordering: int | EmptyField = Empty,
        action: str | EmptyField = Empty
    ):
        return BoundRowFields[Self](cls, {
            cls.id: action_id,
            cls.checklist_item: checklist_item_id,
            cls.ordering: ordering,
            cls.action: action
        })


class ActiveReminders(BaseModel):
    """
    Contains the nearest future reminder for every checklist
    that needs to be sent
    """
    id = AutoField(primary_key=True)
    action_id = ForeignKeyField(
        ChecklistItemActions, to_field='id', on_delete='CASCADE'
    )
    checklist = ForeignKeyField(Checklist, to_field='id', on_delete='CASCADE')
    scheduled_time = DateTimeField(null=False)

    class Meta:
        # index scheduled_time (but also it can't be unique because
        # multiple reminders can be scheduled for the same time)
        indexes = (
            (("scheduled_time",), False),
        )
