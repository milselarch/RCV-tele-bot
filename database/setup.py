from peewee import MySQLDatabase, Proxy
from playhouse.shortcuts import ReconnectMixin

from database.db_helpers import TypedModel


class DB(ReconnectMixin, MySQLDatabase):
    pass


database_proxy = Proxy()


class BaseModel(TypedModel):
    class Meta:
        database = database_proxy
        table_settings = ['DEFAULT CHARSET=utf8mb4']

