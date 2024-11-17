import pytest

from peewee import SqliteDatabase
from database import database


@pytest.fixture(scope='function')
def test_database():
    test_db = SqliteDatabase(':memory:')
    # Bind model classes to test database
    database.initialize_db(test_db)
    db_tables = database.get_tables()
    test_db.create_tables(db_tables)

    yield test_db
    test_db.drop_tables(db_tables)
    test_db.close()
