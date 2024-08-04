from __future__ import annotations

import peewee
import datetime

from playhouse.shortcuts import ReconnectMixin
from load_config import YAML_CONFIG

from typing import Iterable, Self
from database.db_helpers import (
    ModelRowFields, TypedModel, BoundModelRowFields
)
from peewee import (
    MySQLDatabase, BigIntegerField, CharField,
    IntegerField, AutoField, TextField, DateTimeField,
    BooleanField, ForeignKeyField, SQL
)


class DB(ReconnectMixin, MySQLDatabase):
    pass


db = DB(
    database='ranked_choice_voting',
    user=YAML_CONFIG['database']['user'],
    password=YAML_CONFIG['database']['password']
)


class BaseModel(TypedModel):
    DoesNotExist: peewee.DoesNotExist

    class Meta:
        database = db

    @classmethod
    def batch_insert(cls, row_entries: Iterable[ModelRowFields]):
        rows = [row_entry.to_dict() for row_entry in row_entries]
        return cls.insert_many(rows)


# maps telegram user ids to their usernames
class Users(BaseModel):
    id = BigIntegerField(primary_key=True)
    # telegram user id
    tele_id = BigIntegerField(null=False)
    username = CharField(max_length=255, default=None, null=True)
    credits = IntegerField(default=0)
    subscription_tier = IntegerField(default=0)

    class Meta:
        database = db
        indexes = (
            # Non-unique index for usernames
            # telegram usernames have to be unique, however because
            # every username changes can't be tracked instantly
            # it possible there will be collisions here regardless
            (('username',), False),
        )

    @classmethod
    def build_row_from_fields(
        cls, tele_id: int, username: str | None = None
    ) -> BoundModelRowFields[Self]:
        return BoundModelRowFields(cls, {
            cls.id: tele_id, cls.tele_id: tele_id,
            cls.username: username
        })


# stores poll metadata (description, open time, etc etc)
class Polls(BaseModel):
    id = AutoField(primary_key=True)
    desc = TextField(default="")
    close_time = DateTimeField(default=None)
    open_time = DateTimeField(default=datetime.datetime.now)
    closed = BooleanField(default=False)

    # whether users are allowed to self register for the poll
    open_registration = BooleanField(default=False)
    auto_refill = BooleanField(default=False)

    # telegram user id of poll creator
    creator_id = ForeignKeyField(Users, to_field='id', on_delete='CASCADE')
    max_voters = IntegerField(default=10)
    # number of registered voters in the poll
    num_voters = IntegerField(default=0)
    # number of registered votes in the poll
    num_votes = IntegerField(default=0)


# whitelisted group chats from which users are
# allowed to register as voters for a poll
class ChatWhitelist(BaseModel):
    id = AutoField(primary_key=True)
    poll_id = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    chat_id = BigIntegerField()  # telegram chat ID
    broadcasted = BooleanField(default=False)

    class Meta:
        database = db
        indexes = (
            # Unique multi-column index for poll_id-chat_id pairs
            (('poll_id', 'chat_id'), True),
        )


class PollVoters(BaseModel):
    id = AutoField(primary_key=True)
    # poll that voter is eligible to vote for
    poll_id = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    # telegram user id of voter
    user_id = ForeignKeyField(Users, to_field='id', on_delete='CASCADE')
    voted = BooleanField(default=False)

    class Meta:
        database = db
        indexes = (
            # Unique multi-column index for poll_id-user_id pairs
            (('poll_id', 'user_id'), True),
        )


# whitelists voters for a poll by their username
# assigns their user_id to the corresponding username
# when they cast a vote (used to check for duplicate votes later)
class UsernameWhitelist(BaseModel):
    id = AutoField(primary_key=True)
    # username of whitelisted telegram user
    username = CharField(max_length=255)
    # poll that voter is eligible to vote for
    poll_id = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    # telegram user id of voter
    user_id = ForeignKeyField(
        Users, to_field='id', null=True, on_delete='CASCADE'
    )

    class Meta:
        database = db
        indexes = (
            # Unique multi-column index for poll_id-username pairs
            (('poll_id', 'username'), True),
        )


class PollOptions(BaseModel):
    id = AutoField(primary_key=True)
    poll_id = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    option_name = CharField(max_length=255)
    option_number = IntegerField()


class VoteRankings(BaseModel):
    id = AutoField(primary_key=True)
    poll_voter_id = ForeignKeyField(
        PollVoters, to_field='id', on_delete='CASCADE'
    )
    # ID of the corresponding poll option for the vote
    option_id = ForeignKeyField(
        PollOptions, to_field='id', null=True, on_delete='CASCADE'
    )
    # special vote value that doesn't map to any of the poll options
    # currently the special votes are 0 and nil votes
    special_value = IntegerField(
        constraints=[SQL("CHECK (special_value < 0)")], null=True
    )
    ranking = IntegerField()


# Create tables (if they don't exist)
db.connect()
db.create_tables([
    Users, Polls, ChatWhitelist, PollVoters, UsernameWhitelist,
    PollOptions, VoteRankings
], safe=True)

