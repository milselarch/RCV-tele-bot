import yaml
import datetime

from peewee import *
from playhouse.shortcuts import ReconnectMixin

with open('config.yml', 'r') as config_file:
    yaml_data = yaml.safe_load(config_file)

username = yaml_data['database']['user']
password = yaml_data['database']['password']


class DB(ReconnectMixin, MySQLDatabase):
    pass


db = DB(
    database='ranked_choice_voting', user=username,
    password=password
)


class BaseModel(Model):
    class Meta:
        database = db


# maps telegram user ids to thier usernames
class Users(BaseModel):
    # telegram user id
    id = IntegerField(primary_key=True)
    username = CharField(max_length=255, default=None)

    class Meta:
        database = db
        indexes = (
            # Non-unique index for usernames
            # telegram usernames have to be unique, however because
            # every username changes can't be tracked instantly
            # it possible there will be collisions here regardless
            (('username',), False),
        )


# stores poll metadata (description, open time, etc etc)
class Polls(BaseModel):
    id = AutoField(primary_key=True)
    desc = TextField(default="")
    close_time = TimestampField(default=None)
    open_time = TimestampField(default=datetime.datetime.now)
    closed = BooleanField(default=False)

    # creator = CharField(max_length=255, default=None)
    # telegram user id of poll creator
    creator_id = IntegerField()
    # number of registered voters in the poll
    num_voters = IntegerField()
    # number of registered votes in the poll
    num_votes = IntegerField()


# whitelisted group chats from which users are
# allowed to register as voters for a poll
class ChatWhitelist(BaseModel):
    id = AutoField(primary_key=True)
    poll_id = ForeignKeyField(Polls, to_field='id')
    tele_id = IntegerField()
    broadcasted = BooleanField(default=False)


class PollVoters(BaseModel):
    id = AutoField(primary_key=True)
    # poll that voter is eligible to vote for
    poll_id = ForeignKeyField(Polls, to_field='id')
    # telegram user id of voter
    user_id = ForeignKeyField(Users, to_field='id')
    voted = BooleanField(default=False)

    class Meta:
        database = db
        indexes = (
            # Non-unique multi-column index for poll_id-user_id pairs
            # (technically it should only be non-unique when user_id is None)
            (('poll_id', 'user_id'), False),
        )


# whitelists voters for a poll by their username
# assigns their user_id to the corresponding username
# when they cast a vote (used to check for duplicate votes later)
class UsernameWhitelist(BaseModel):
    username = CharField()
    # poll that voter is eligible to vote for
    poll_id = ForeignKeyField(Polls, to_field='id')
    # telegram user id of voter
    user_id = ForeignKeyField(Users, to_field='id', null=True)

    class Meta:
        database = db
        indexes = (
            # Unique multi-column index for poll_id-username pairs
            (('poll_id', 'username'), True),
        )


class PollOptions(BaseModel):
    id = AutoField(primary_key=True)
    poll_id = ForeignKeyField(Polls, to_field='id')
    option_name = CharField(max_length=255)
    option_number = IntegerField()


class Votes(BaseModel):
    id = AutoField(primary_key=True)
    poll_id = ForeignKeyField(Polls, to_field='id')
    poll_voter_id = ForeignKeyField(PollVoters, to_field='id')
    # ID of the corresponding poll option for the vote
    option_id = ForeignKeyField(PollOptions, to_field='id', null=True)
    # special vote value that doesn't map to any of the poll options
    # currently the special votes are 0 and nil votes
    special_value = IntegerField(
        constraints=[SQL("CHECK (special_value < 0)")], null=True
    )
    ranking = IntegerField()


# Create tables (if they don't exist)
db.connect()
db.create_tables([
    Polls, ChatWhitelist, PollVoters, PollOptions, Votes
], safe=True)

