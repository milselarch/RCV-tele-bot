from __future__ import annotations

import datetime

from playhouse.shortcuts import ReconnectMixin
from result import Result, Ok
from load_config import YAML_CONFIG
from typing import Self, Optional
from database.db_helpers import (
    BoundRowFields, Empty, EmptyField, TypedModel
)
from peewee import (
    MySQLDatabase, BigIntegerField, CharField,
    IntegerField, AutoField, TextField, DateTimeField,
    BooleanField, ForeignKeyField, SQL, BigAutoField, Proxy
)


class DB(ReconnectMixin, MySQLDatabase):
    pass


database_proxy = Proxy()
initialised_db: DB | None = None


def initialize_db(db: DB | None = None):
    if db is None:
        db = DB(
            database='ranked_choice_voting',
            user=YAML_CONFIG['database']['user'],
            password=YAML_CONFIG['database']['password'],
            charset='utf8mb4'
        )

    database_proxy.initialize(db)
    global initialised_db
    initialised_db = db

    # Create tables (if they don't exist)
    database_proxy.connect()
    database_proxy.create_tables([
        Users, Polls, ChatWhitelist, PollVoters, UsernameWhitelist,
        PollOptions, VoteRankings, PollWinners
    ], safe=True)


class BaseModel(TypedModel):
    class Meta:
        database = database_proxy
        table_settings = ['DEFAULT CHARSET=utf8mb4']


class UserID(int):
    pass


# maps telegram user ids to their usernames
class Users(BaseModel):
    id = BigAutoField(primary_key=True)
    # telegram user id
    tele_id = BigIntegerField(null=False, index=True, unique=True)
    username = CharField(max_length=255, default=None, null=True)
    credits = IntegerField(default=0)
    subscription_tier = IntegerField(default=0)

    class Meta:
        database = database_proxy
        indexes = (
            # Non-unique index for usernames
            # telegram usernames have to be unique, however because
            # every username changes can't be tracked instantly
            # it possible there will be collisions here regardless
            (('username',), False),
        )

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
    creator = ForeignKeyField(Users, to_field='id', on_delete='CASCADE')
    max_voters = IntegerField(default=10)
    # number of registered voters in the poll
    num_voters = IntegerField(default=0)
    # number of registered votes in the poll
    num_votes = IntegerField(default=0)

    def get_creator(self) -> Users:
        # TODO: do a unit test for this
        assert isinstance(self.creator, Users)
        return self.creator

    def get_creator_id(self) -> UserID:
        return self.get_creator().get_user_id()

    @classmethod
    def build_from_fields(
        cls, desc: str | EmptyField = Empty,
        creator_id: UserID | EmptyField = Empty,
        num_voters: int | EmptyField = Empty,
        open_registration: bool | EmptyField = Empty,
        max_voters: int | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.desc: desc, cls.creator: creator_id,
            cls.num_voters: num_voters,
            cls.open_registration: open_registration,
            cls.max_voters: max_voters
        })


# whitelisted group chats from which users are
# allowed to register as voters for a poll
class ChatWhitelist(BaseModel):
    id = AutoField(primary_key=True)
    poll = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    chat_id = BigIntegerField()  # telegram chat ID
    broadcasted = BooleanField(default=False)

    class Meta:
        database = database_proxy
        indexes = (
            # Unique multi-column index for poll_id-chat_id pairs
            (('poll', 'chat_id'), True),
        )

    @classmethod
    def build_from_fields(
        cls, poll_id: int | EmptyField = Empty,
        chat_id: int | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.poll: poll_id, cls.chat_id: chat_id
        })


class PollVoters(BaseModel):
    id = AutoField(primary_key=True)
    # poll that voter is eligible to vote for
    poll = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    # telegram user id of voter
    user = ForeignKeyField(Users, to_field='id', on_delete='CASCADE')
    voted = BooleanField(default=False)

    class Meta:
        database = database_proxy
        indexes = (
            # Unique multi-column index for poll_id-user_id pairs
            (('poll', 'user'), True),
        )

    @classmethod
    def build_from_fields(
        cls, user_id: UserID | EmptyField = Empty,
        poll_id: int | EmptyField = Empty,
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.user: user_id, cls.poll: poll_id
        })

    def get_voter_user(self) -> Users:
        # TODO: do a unit test for this
        assert isinstance(self.user, Users)
        return self.user


# whitelists voters for a poll by their username
# assigns their user_id to the corresponding username
# when they cast a vote (used to check for duplicate votes later)
class UsernameWhitelist(BaseModel):
    id = AutoField(primary_key=True)
    # username of whitelisted telegram user
    username = CharField(max_length=255)
    # poll that voter is eligible to vote for
    poll = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    # telegram user id of voter
    user = ForeignKeyField(
        Users, to_field='id', null=True, on_delete='CASCADE'
    )

    class Meta:
        database = database_proxy
        indexes = (
            # Unique multi-column index for poll_id-username pairs
            (('poll', 'username'), True),
        )

    @classmethod
    def build_from_fields(
        cls, username: str | EmptyField = Empty,
        poll_id: int | EmptyField = Empty,
        user_id: UserID | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.username: username, cls.poll: poll_id, cls.user: user_id
        })


class PollOptions(BaseModel):
    id = AutoField(primary_key=True)
    poll = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    option_name = CharField(max_length=255)
    option_number = IntegerField()

    @classmethod
    def build_from_fields(
        cls, poll_id: int | EmptyField = Empty,
        option_name: str | EmptyField = Empty,
        option_number: int | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.poll: poll_id, cls.option_name: option_name,
            cls.option_number: option_number
        })


class VoteRankings(BaseModel):
    id = AutoField(primary_key=True)
    poll_voter = ForeignKeyField(
        PollVoters, to_field='id', on_delete='CASCADE'
    )
    # ID of the corresponding poll option for the vote
    option = ForeignKeyField(
        PollOptions, to_field='id', null=True, on_delete='CASCADE'
    )
    # special vote value that doesn't map to any of the poll options
    # currently the special votes are 0 and nil votes
    special_value = IntegerField(
        constraints=[SQL("CHECK (special_value < 0)")], null=True
    )
    ranking = IntegerField()


class PollWinners(BaseModel):
    id = AutoField(primary_key=True)
    poll = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    option = ForeignKeyField(
        PollOptions, to_field='id', on_delete='CASCADE',
        null=True
    )

    @classmethod
    def build_from_fields(
        cls, poll_id: int | EmptyField = Empty,
        option_id: int | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.poll: poll_id, cls.option: option_id
        })

    @classmethod
    def read_poll_winner_id(cls, poll_id: int) -> Result[Optional[int]]:
        get_result = cls.build_from_fields(poll_id=poll_id).safe_get()
        if get_result.is_err():
            return get_result

        poll_winner = get_result.unwrap()
        winning_option = poll_winner.option
        if winning_option is None:
            return Ok(None)

        winning_option_id = int(winning_option.id)
        return Ok(winning_option_id)


if __name__ == '__main__':
    # Create tables (if they don't exist)
    initialize_db()

