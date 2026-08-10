"""Peewee migrations -- 003_migrations.py.

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
    class Checklist(pw.Model):
        id = pw.AutoField()
        owner = pw.ForeignKeyField(column_name='owner_id', field='id', model=migrator.orm['users'], on_delete='CASCADE')
        name = pw.CharField(max_length=255)

        class Meta:
            table_name = "checklist"

    @migrator.create_model
    class ChecklistItem(pw.Model):
        id = pw.AutoField()
        checklist = pw.ForeignKeyField(column_name='checklist_id', field='id', model=migrator.orm['checklist'], on_delete='CASCADE')
        parent_item = pw.ForeignKeyField(column_name='parent_item_id', field='id', model='self', null=True, on_delete='CASCADE')
        name = pw.CharField(max_length=255)
        ordering = pw.IntegerField()
        last_checked = pw.DateTimeField()
        last_unchecked = pw.DateTimeField()
        last_reminder = pw.DateTimeField()

        class Meta:
            table_name = "checklistitem"
            indexes = [(('checklist', 'parent_item', 'ordering'), True)]

    @migrator.create_model
    class ChecklistItemActions(pw.Model):
        id = pw.AutoField()
        checklist_item = pw.ForeignKeyField(column_name='checklist_item_id', field='id', model=migrator.orm['checklistitem'], on_delete='CASCADE')
        ordering = pw.IntegerField()
        action = pw.TextField()

        class Meta:
            table_name = "checklistitemactions"
            indexes = [(('checklist_item', 'ordering'), True)]

    @migrator.create_model
    class ActiveReminders(pw.Model):
        id = pw.AutoField()
        action_id = pw.ForeignKeyField(column_name='action_id', field='id', model=migrator.orm['checklistitemactions'], on_delete='CASCADE')
        checklist = pw.ForeignKeyField(column_name='checklist_id', field='id', model=migrator.orm['checklist'], on_delete='CASCADE')
        scheduled_time = pw.DateTimeField()

        class Meta:
            table_name = "activereminders"


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    """Write your rollback migrations here."""
    
    migrator.remove_model('activereminders')

    migrator.remove_model('checklistitemactions')

    migrator.remove_model('checklistitem')

    migrator.remove_model('checklist')
