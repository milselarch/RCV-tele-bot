from peewee import (
    AutoField, ForeignKeyField, CharField,
    IntegerField, DateTimeField, TextField
)
from database import Users
from database.setup import BaseModel


class Checklist(BaseModel):
    id = AutoField(primary_key=True)
    owner = ForeignKeyField(Users, to_field='id', on_delete='CASCADE')
    name = CharField(max_length=255)


class ChecklistItem(BaseModel):
    id = AutoField(primary_key=True)
    checklist = ForeignKeyField(Checklist, to_field='id', on_delete='CASCADE')
    parent_item = ForeignKeyField(
        'self', to_field='id', null=True, on_delete='CASCADE'
    )
    name = CharField(max_length=255)

    # TODO: require that ordering is unique within a checklist / parent item
    ordering = IntegerField()
    last_checked = DateTimeField(default=None)
    last_unchecked = DateTimeField(default=None)
    last_reminder = DateTimeField(default=None)

    class Meta:
        # make sure every checklist item has a unique ordering
        # within items with the same (checklist, parent item)
        indexes = (
            (("checklist", "parent_item", "ordering"), True),
        )


class ChecklistItemActions(BaseModel):
    id = AutoField(primary_key=True)
    checklist_item = ForeignKeyField(
        ChecklistItem, to_field='id', on_delete='CASCADE'
    )
    ordering = IntegerField()
    action_type = CharField(max_length=255)
    state = TextField(null=False)

    class Meta:
        # make sure every action has a unique ordering
        # within actions with the same parent checklist_item
        indexes = (
            (("checklist_item", "ordering"), True),
        )


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
