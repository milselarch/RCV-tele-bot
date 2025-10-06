"""Peewee migrations -- 001_migrations.py.

Some examples (model - class or model name)::

    > Model = migrator.orm['table_name']            # Return model in current state by name
    > Model = migrator.ModelClass                   # Return model in current state by name

    > migrator.sql(sql)                             # Run custom SQL
    > migrator.run(func, *args, **kwargs)           # Run python function with the given args
    > migrator.create_model(Model)                  # Create a model (could be used as decorator)
    > migrator.remove_model(model, cascade=True)    # Remove a model
    > migrator.add_fields(model, **fields)          # Add fields to a model
    > migrator.change_fields(model, **fields)       # Change fields
    > migrator.remove_fields(model, *field_names, cascade=True)
    > migrator.rename_field(model, old_field_name, new_field_name)
    > migrator.rename_table(model, new_table_name)
    > migrator.add_index(model, *col_names, unique=False)
    > migrator.add_not_null(model, *field_names)
    > migrator.add_default(model, field_name, default)
    > migrator.add_constraint(model, name, sql)
    > migrator.drop_index(model, *col_names)
    > migrator.drop_not_null(model, *field_names)
    > migrator.drop_constraints(model, *constraints)

"""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator


with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    """Write your migrations here."""
    
    @migrator.create_model
    class BaseModel(pw.Model):
        id = pw.AutoField()

        class Meta:
            table_name = "basemodel"

    @migrator.create_model
    class Users(pw.Model):
        id = pw.BigAutoField()
        tele_id = pw.BigIntegerField(unique=True)
        username = pw.CharField(max_length=255, null=True)
        credits = pw.IntegerField(default=0)
        subscription_tier = pw.IntegerField(default=0)
        deleted_at = pw.DateTimeField(null=True)

        class Meta:
            table_name = "users"
            indexes = [(('username',), False)]

    @migrator.create_model
    class CallbackContextState(pw.Model):
        id = pw.BigAutoField()
        user = pw.ForeignKeyField(column_name='user_id', field='id', model=migrator.orm['users'], on_delete='CASCADE')
        chat_id = pw.BigIntegerField()
        context_type = pw.CharField(max_length=255)
        state = pw.TextField()
        last_updated_at = pw.DateTimeField()

        class Meta:
            table_name = "callbackcontextstate"

    @migrator.create_model
    class Polls(pw.Model):
        id = pw.AutoField()
        desc = pw.TextField(default='')
        close_time = pw.DateTimeField()
        open_time = pw.DateTimeField()
        closed = pw.BooleanField(default=False)
        open_registration = pw.BooleanField(default=False)
        auto_refill = pw.BooleanField(default=False)
        creator = pw.ForeignKeyField(column_name='creator_id', field='id', model=migrator.orm['users'], on_delete='CASCADE')
        max_voters = pw.IntegerField(default=10)
        num_voters = pw.IntegerField(default=0)
        num_votes = pw.IntegerField(default=0)
        deleted_voters = pw.IntegerField(default=0)

        class Meta:
            table_name = "polls"

    @migrator.create_model
    class ChatWhitelist(pw.Model):
        id = pw.AutoField()
        poll = pw.ForeignKeyField(column_name='poll_id', field='id', model=migrator.orm['polls'], on_delete='CASCADE')
        chat_id = pw.BigIntegerField()
        broadcasted = pw.BooleanField(default=False)

        class Meta:
            table_name = "chatwhitelist"
            indexes = [(('poll', 'chat_id'), True)]

    @migrator.create_model
    class MessageContextState(pw.Model):
        id = pw.BigAutoField()
        user = pw.ForeignKeyField(column_name='user_id', field='id', model=migrator.orm['users'], on_delete='CASCADE')
        message_id = pw.BigIntegerField()
        context_type = pw.CharField(max_length=255)
        state = pw.TextField()
        last_updated_at = pw.DateTimeField()

        class Meta:
            table_name = "messagecontextstate"

    @migrator.create_model
    class Payments(pw.Model):
        id = pw.AutoField()
        user_id = pw.BigIntegerField()
        telegram_payment_charge_id = pw.CharField(default='', index=True, max_length=255)
        amount = pw.IntegerField()
        paid = pw.BooleanField(default=False)
        processed = pw.BooleanField(default=False)
        invoice_payload = pw.TextField(default='')
        created_at = pw.DateTimeField()
        refunded_at = pw.DateTimeField(null=True)
        refund_amount = pw.IntegerField(default=0)

        class Meta:
            table_name = "payments"

    @migrator.create_model
    class PollOptions(pw.Model):
        id = pw.AutoField()
        poll = pw.ForeignKeyField(column_name='poll_id', field='id', model=migrator.orm['polls'], on_delete='CASCADE')
        option_name = pw.CharField(max_length=255)
        option_number = pw.IntegerField()

        class Meta:
            table_name = "polloptions"

    @migrator.create_model
    class PollVoters(pw.Model):
        id = pw.AutoField()
        poll = pw.ForeignKeyField(column_name='poll_id', field='id', model=migrator.orm['polls'], on_delete='CASCADE')
        user = pw.ForeignKeyField(column_name='user_id', field='id', model=migrator.orm['users'], null=True, on_delete='CASCADE')
        voted = pw.BooleanField(default=False)

        class Meta:
            table_name = "pollvoters"
            indexes = [(('poll', 'user'), True)]

    @migrator.create_model
    class PollWinners(pw.Model):
        id = pw.AutoField()
        poll = pw.ForeignKeyField(column_name='poll_id', field='id', model=migrator.orm['polls'], on_delete='CASCADE')
        option = pw.ForeignKeyField(column_name='option_id', field='id', model=migrator.orm['polloptions'], null=True, on_delete='CASCADE')

        class Meta:
            table_name = "pollwinners"

    @migrator.create_model
    class SupportTickets(pw.Model):
        id = pw.BigAutoField()
        info = pw.TextField()
        is_payment_support = pw.BooleanField(default=False)
        resolved = pw.BooleanField(default=False)

        class Meta:
            table_name = "supporttickets"

    @migrator.create_model
    class TypedModel(pw.Model):
        id = pw.AutoField()

        class Meta:
            table_name = "typedmodel"

    @migrator.create_model
    class UsernameWhitelist(pw.Model):
        id = pw.AutoField()
        username = pw.CharField(max_length=255)
        poll = pw.ForeignKeyField(column_name='poll_id', field='id', model=migrator.orm['polls'], on_delete='CASCADE')
        user = pw.ForeignKeyField(column_name='user_id', field='id', model=migrator.orm['users'], null=True, on_delete='CASCADE')

        class Meta:
            table_name = "usernamewhitelist"
            indexes = [(('poll', 'username'), True)]

    @migrator.create_model
    class VoteRankings(pw.Model):
        id = pw.AutoField()
        poll_voter = pw.ForeignKeyField(column_name='poll_voter_id', field='id', model=migrator.orm['pollvoters'], on_delete='CASCADE')
        option = pw.ForeignKeyField(column_name='option_id', field='id', model=migrator.orm['polloptions'], null=True, on_delete='CASCADE')
        special_value = pw.IntegerField(null=True)
        ranking = pw.IntegerField()

        class Meta:
            table_name = "voterankings"


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    """Write your rollback migrations here."""
    
    migrator.remove_model('voterankings')

    migrator.remove_model('users')

    migrator.remove_model('usernamewhitelist')

    migrator.remove_model('typedmodel')

    migrator.remove_model('supporttickets')

    migrator.remove_model('pollwinners')

    migrator.remove_model('pollvoters')

    migrator.remove_model('polloptions')

    migrator.remove_model('payments')

    migrator.remove_model('messagecontextstate')

    migrator.remove_model('chatwhitelist')

    migrator.remove_model('polls')

    migrator.remove_model('callbackcontextstate')

    migrator.remove_model('basemodel')
