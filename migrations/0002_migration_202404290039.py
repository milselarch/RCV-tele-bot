# auto-generated snapshot
from peewee import *
import datetime
import peewee


snapshot = Snapshot()


@snapshot.append
class Polls(peewee.Model):
    desc = TextField(default='')
    close_time = TimestampField()
    open_time = TimestampField(default=datetime.datetime.now)
    closed = BooleanField(default=False)
    creator = CharField(max_length=255)
    class Meta:
        table_name = "polls"


@snapshot.append
class Chats(peewee.Model):
    poll_id = snapshot.ForeignKeyField(index=True, model='polls')
    tele_id = IntegerField()
    broadcasted = BooleanField(default=False)
    class Meta:
        table_name = "chats"


@snapshot.append
class Options(peewee.Model):
    poll_id = snapshot.ForeignKeyField(index=True, model='polls')
    option_name = CharField(max_length=255)
    option_number = IntegerField()
    class Meta:
        table_name = "options"


@snapshot.append
class PollVoters(peewee.Model):
    poll_id = snapshot.ForeignKeyField(index=True, model='polls')
    username = CharField(max_length=255)
    user_id = IntegerField(null=True)
    class Meta:
        table_name = "pollvoters"
        indexes = (
            (('poll_id', 'username'), True),
            )


@snapshot.append
class Votes(peewee.Model):
    poll_id = snapshot.ForeignKeyField(index=True, model='polls')
    poll_voter_id = snapshot.ForeignKeyField(index=True, model='pollvoters')
    option_id = snapshot.ForeignKeyField(index=True, model='options', null=True)
    special_value = IntegerField(constraints=[SQL('CHECK (special_value < 0)')], null=True)
    ranking = IntegerField()
    class Meta:
        table_name = "votes"


