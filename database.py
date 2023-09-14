import datetime

from peewee import *

db = MySQLDatabase(
    'ranked_choice_voting'
)


class BaseModel(Model):
    class Meta:
        database = db


class Polls(Model):
    id = IntegerField(primary_key=True)
    desc = CharField()
    close_time = TimestampField()
    open_time = TimestampField()
    closed = BooleanField()


class Groups(Model):
    id = IntegerField(primary_key=True)
    poll_id = ForeignKeyField(Polls, backref='groups')
    group_id = IntegerField()
    broadcasted = BooleanField()


class PollUsers(Model):
    id = IntegerField(primary_key=True)
    poll_id = ForeignKeyField(Polls, backref='poll_users')


class Options(Model):
    id = IntegerField(primary_key=True)
    poll_id = ForeignKeyField(Polls, backref='options')
    option_name = CharField(max_length=255)


class Votes(Model):
    id = IntegerField(primary_key=True)
    poll_id = ForeignKeyField(Polls, backref='votes')
    poll_user_id = IntegerField()
    option_id = IntegerField()
    ranking = IntegerField()


# Create tables (if they don't exist)
db.connect()
db.create_tables([
    Polls, Groups, PollUsers, Options, Votes
], safe=True)

# Define foreign keys
Polls.add_foreign_key('poll_user_id', PollUsers)
Polls.add_foreign_key('option_id', Options)
Votes.add_foreign_key('poll_user_id', PollUsers)
Votes.add_foreign_key('option_id', Options)
