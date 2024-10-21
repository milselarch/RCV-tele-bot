# auto-generated snapshot
from peewee import *
import datetime
import peewee


snapshot = Snapshot()


@snapshot.append
class Users(peewee.Model):
    id = BigAutoField(primary_key=True)
    tele_id = BigIntegerField(index=True, unique=True)
    username = CharField(max_length=255, null=True)
    credits = IntegerField(default=0)
    subscription_tier = IntegerField(default=0)
    deleted_at = DateTimeField(null=True)
    class Meta:
        table_name = "users"
        indexes = (
            (('username',), False),
            )


@snapshot.append
class CallbackContextState(peewee.Model):
    id = BigAutoField(primary_key=True)
    user = snapshot.ForeignKeyField(index=True, model='users', on_delete='CASCADE')
    chat_id = BigIntegerField()
    context_type = CharField(max_length=255)
    state = TextField()
    class Meta:
        table_name = "callbackcontextstate"


@snapshot.append
class Polls(peewee.Model):
    desc = TextField(default='')
    close_time = DateTimeField()
    open_time = DateTimeField(default=datetime.datetime.now)
    closed = BooleanField(default=False)
    open_registration = BooleanField(default=False)
    auto_refill = BooleanField(default=False)
    creator = snapshot.ForeignKeyField(index=True, model='users', on_delete='CASCADE')
    max_voters = IntegerField(default=10)
    num_voters = IntegerField(default=0)
    num_votes = IntegerField(default=0)
    deleted_voters = IntegerField(default=0)
    class Meta:
        table_name = "polls"


@snapshot.append
class ChatWhitelist(peewee.Model):
    poll = snapshot.ForeignKeyField(index=True, model='polls', on_delete='CASCADE')
    chat_id = BigIntegerField()
    broadcasted = BooleanField(default=False)
    class Meta:
        table_name = "chatwhitelist"
        indexes = (
            (('poll', 'chat_id'), True),
            )


@snapshot.append
class PollOptions(peewee.Model):
    poll = snapshot.ForeignKeyField(index=True, model='polls', on_delete='CASCADE')
    option_name = CharField(max_length=255)
    option_number = IntegerField()
    class Meta:
        table_name = "polloptions"


@snapshot.append
class PollVoters(peewee.Model):
    poll = snapshot.ForeignKeyField(index=True, model='polls', on_delete='CASCADE')
    user = snapshot.ForeignKeyField(index=True, model='users', null=True, on_delete='CASCADE')
    voted = BooleanField(default=False)
    class Meta:
        table_name = "pollvoters"
        indexes = (
            (('poll', 'user'), True),
            )


@snapshot.append
class PollWinners(peewee.Model):
    poll = snapshot.ForeignKeyField(index=True, model='polls', on_delete='CASCADE')
    option = snapshot.ForeignKeyField(index=True, model='polloptions', null=True, on_delete='CASCADE')
    class Meta:
        table_name = "pollwinners"


@snapshot.append
class UsernameWhitelist(peewee.Model):
    username = CharField(max_length=255)
    poll = snapshot.ForeignKeyField(index=True, model='polls', on_delete='CASCADE')
    user = snapshot.ForeignKeyField(index=True, model='users', null=True, on_delete='CASCADE')
    class Meta:
        table_name = "usernamewhitelist"
        indexes = (
            (('poll', 'username'), True),
            )


@snapshot.append
class VoteRankings(peewee.Model):
    poll_voter = snapshot.ForeignKeyField(index=True, model='pollvoters', on_delete='CASCADE')
    option = snapshot.ForeignKeyField(index=True, model='polloptions', null=True, on_delete='CASCADE')
    special_value = IntegerField(constraints=[SQL('CHECK (special_value < 0)')], null=True)
    ranking = IntegerField()
    class Meta:
        table_name = "voterankings"


