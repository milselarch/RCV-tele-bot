from __future__ import annotations

import dataclasses
import datetime
import os
import sys

# noinspection PyUnresolvedReferences
from playhouse.shortcuts import ReconnectMixin
from result import Result, Ok, Err

from database.setup import DB, BaseModel, database_proxy
from database.users import Users
from database.payments import Payments
from database.callback_context_state import CallbackContextState
from database.message_context_state import MessageContextState

from load_config import YAML_CONFIG
from typing import Self, Optional, Type, List
from database.db_helpers import (
    BoundRowFields, Empty, EmptyField, UserID
)
from peewee import (
    BigIntegerField, CharField,
    IntegerField, AutoField, TextField, DateTimeField,
    BooleanField, ForeignKeyField, SQL, Database, BigAutoField,
)

initialised_db: DB | None = None
# TODO: refactor each individual table into its own file


def get_tables() -> list[Type[BaseModel]]:
    return [
        Users, Polls, ChatWhitelist, PollVoters, UsernameWhitelist,
        PollOptions, VoteRankings, PollWinners, CallbackContextState,
        MessageContextState, Payments, SupportTickets
    ]


def initialize_db(db: Database | None = None):
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
    database_proxy.create_tables(get_tables(), safe=True)


@dataclasses.dataclass
class PollMetadata(object):
    id: int
    question: str
    _num_voters: int
    num_deleted: int
    num_votes: int
    max_voters: int

    open_registration: bool
    closed: bool

    @property
    def num_active_voters(self) -> int:
        return self._num_voters - self.num_deleted


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
    # TODO: rename to raw_num_voters or _num_voters to make it clear
    #   that this number includes deleted voters as well
    num_voters = IntegerField(default=0)
    # number of registered votes in the poll
    num_votes = IntegerField(default=0)
    # TODO: rename to num_deleted_voters
    deleted_voters = IntegerField(default=0)

    @property
    def num_active_voters(self) -> int:
        assert isinstance(self.num_voters, int)
        assert isinstance(self.deleted_voters, int)
        return self.num_voters - self.deleted_voters

    def get_creator(self) -> Users:
        # TODO: do a unit test for this
        assert isinstance(self.creator, Users)
        return self.creator

    def get_creator_id(self) -> UserID:
        return self.get_creator().get_user_id()

    @classmethod
    def get_is_closed(cls, poll_id: int) -> Result[bool, None]:
        try:
            poll = cls.select().where(cls.id == poll_id).get()
        except Polls.DoesNotExist:
            return Err(None)

        return Ok(poll.closed)

    @classmethod
    def read_poll_metadata(cls, poll_id: int) -> PollMetadata:
        poll = cls.select().where(cls.id == poll_id).get()
        return PollMetadata(
            id=poll.id, question=poll.desc,
            _num_voters=poll.num_voters, num_votes=poll.num_votes,
            open_registration=poll.open_registration,
            closed=poll.closed, num_deleted=poll.deleted_voters,
            max_voters=poll.max_voters
        )

    @classmethod
    def build_from_fields(
        cls, poll_id: int | EmptyField = Empty,
        desc: str | EmptyField = Empty,
        creator_id: UserID | EmptyField = Empty,
        num_voters: int | EmptyField = Empty,
        open_registration: bool | EmptyField = Empty,
        max_voters: int | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.id: poll_id, cls.desc: desc, cls.creator: creator_id,
            cls.num_voters: num_voters,
            cls.open_registration: open_registration,
            cls.max_voters: max_voters
        })

    @classmethod
    def get_as_creator(
        cls, poll_id: int, user_id: UserID
    ) -> Result[Polls, Polls.DoesNotExist]:
        # TODO: wrap this in a Result with an enum error type
        #   (not found, unauthorized, etc) and use this in
        #   register_user_by_tele_id
        return cls.build_from_fields(
            poll_id=poll_id, creator_id=user_id
        ).safe_get()

    @classmethod
    def count_polls_created(cls, user_id: UserID) -> int:
        return cls.select().where(cls.creator == user_id).count()

    @classmethod
    def get_owned_polls(cls, user_id: UserID) -> List[Polls]:
        return [
            poll for poll in
            cls.select().where(cls.creator == user_id)
        ]


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

    @classmethod
    def is_whitelisted(cls, poll_id: int, chat_id: int) -> bool:
        return ChatWhitelist.build_from_fields(
            poll_id=poll_id, chat_id=chat_id
        ).safe_get().is_ok()


class PollVoters(BaseModel):
    id = AutoField(primary_key=True)
    # poll that voter is eligible to vote for
    poll = ForeignKeyField(Polls, to_field='id', on_delete='CASCADE')
    # telegram user id of voter
    user = ForeignKeyField(
        Users, to_field='id', null=True, on_delete='CASCADE'
    )
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
        voted: bool | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.user: user_id, cls.poll: poll_id,
            cls.voted: voted
        })

    def get_voter_user(self) -> Users:
        # TODO: do a unit test for this
        assert isinstance(self.user, Users)
        return self.user

    @classmethod
    def get_poll_voter(
        cls, poll_id: int, user_id: UserID
    ) -> Result[PollVoters, Optional[BaseModel.DoesNotExist]]:
        # check if voter is part of the poll
        return cls.safe_get(
            (cls.poll == poll_id) & (cls.user == user_id)
        )

    @classmethod
    def is_poll_voter(cls, poll_id: int, user_id: UserID) -> bool:
        return cls.get_poll_voter(poll_id=poll_id, user_id=user_id).is_ok()


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


class SupportTickets(BaseModel):
    id = BigAutoField(primary_key=True)
    info = TextField(null=False)
    is_payment_support = BooleanField(default=False)
    resolved = BooleanField(default=False)

    @classmethod
    def build_from_fields(
        cls, ticket_id: int | EmptyField = Empty,
        info: str | EmptyField = Empty,
        is_payment_support: bool | EmptyField = Empty,
        resolved: bool | EmptyField = Empty
    ) -> BoundRowFields[Self]:
        return BoundRowFields(cls, {
            cls.id: ticket_id, cls.info: info,
            cls.is_payment_support: is_payment_support,
            cls.resolved: resolved
        })


# database should be connected if called from pem db migrations
called_from_pem = os.path.basename(sys.argv[0]) == 'pem'
if (__name__ == '__main__') or called_from_pem:
    print('TESTING')
    # Create tables (if they don't exist)
    initialize_db()
