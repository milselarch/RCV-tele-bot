# auto-generated snapshot
from peewee import *
import datetime
import peewee


snapshot = Snapshot()


@snapshot.append
class Users(peewee.Model):
    id = BigIntegerField(primary_key=True)
    tele_id = BigIntegerField()
    username = CharField(max_length=255, null=True)
    credits = IntegerField(default=0)
    subscription_tier = IntegerField(default=0)
    class Meta:
        table_name = "users"
        indexes = (
            (('username',), False),
            )


@snapshot.append
class Polls(peewee.Model):
    desc = TextField(default='')
    close_time = DateTimeField()
    open_time = DateTimeField(default=datetime.datetime.now)
    closed = BooleanField(default=False)
    open_registration = BooleanField(default=False)
    auto_refill = BooleanField(default=False)
    creator_id = snapshot.ForeignKeyField(index=True, model='users', on_delete='CASCADE')
    max_voters = IntegerField(default=10)
    num_voters = IntegerField(default=0)
    num_votes = IntegerField(default=0)
    class Meta:
        table_name = "polls"


@snapshot.append
class ChatWhitelist(peewee.Model):
    poll_id = snapshot.ForeignKeyField(index=True, model='polls', on_delete='CASCADE')
    chat_id = BigIntegerField()
    broadcasted = BooleanField(default=False)
    class Meta:
        table_name = "chatwhitelist"
        indexes = (
            (('poll_id', 'chat_id'), True),
            )


@snapshot.append
class PollOptions(peewee.Model):
    poll_id = snapshot.ForeignKeyField(index=True, model='polls', on_delete='CASCADE')
    option_name = CharField(max_length=255)
    option_number = IntegerField()
    class Meta:
        table_name = "polloptions"


@snapshot.append
class PollVoters(peewee.Model):
    poll_id = snapshot.ForeignKeyField(index=True, model='polls', on_delete='CASCADE')
    user_id = snapshot.ForeignKeyField(index=True, model='users', on_delete='CASCADE')
    voted = BooleanField(default=False)
    class Meta:
        table_name = "pollvoters"
        indexes = (
            (('poll_id', 'user_id'), True),
            )


@snapshot.append
class UsernameWhitelist(peewee.Model):
    username = CharField(max_length=255)
    poll_id = snapshot.ForeignKeyField(index=True, model='polls', on_delete='CASCADE')
    user_id = snapshot.ForeignKeyField(index=True, model='users', null=True, on_delete='CASCADE')
    class Meta:
        table_name = "usernamewhitelist"
        indexes = (
            (('poll_id', 'username'), True),
            )


@snapshot.append
class VoteRankings(peewee.Model):
    poll_voter_id = snapshot.ForeignKeyField(index=True, model='pollvoters', on_delete='CASCADE')
    option_id = snapshot.ForeignKeyField(index=True, model='polloptions', null=True, on_delete='CASCADE')
    special_value = IntegerField(constraints=[SQL('CHECK (special_value < 0)')], null=True)
    ranking = IntegerField()
    class Meta:
        table_name = "voterankings"


def forward(old_orm, new_orm):
    users = new_orm['users']
    return [
        # Apply default value 0 to the field users.tele_id,
        users.update({users.tele_id: 0}).where(users.tele_id.is_null(True)),
    ]
