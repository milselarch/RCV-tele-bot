from __future__ import annotations

import peewee

from peewee import Model
from typing import (
    Dict, Any, Tuple, Type, TypeVar, Generic, Iterable, TypeAlias
)

from result import Result, Ok, Err


class Empty(object):
    pass


EmptyField: TypeAlias = Type[Empty]


class ModelRowFields(object):
    def __init__(
        self, fields: Dict[peewee.Field, Any],
        filter_any: bool = False
    ):
        self._fields: Dict[str, Any] = {}

        for field, value in fields.items():
            if filter_any and (value is Empty):
                continue

            assert value is not Empty
            if isinstance(field, peewee.ForeignKeyField):
                if isinstance(value, Model):
                    # noinspection PyUnresolvedReferences
                    self._fields[field.name] = value.id
                else:
                    self._fields[field.name] = value
            else:
                self._fields[field.name] = value

    def to_dict(self) -> Dict[str, Any]:
        return self._fields.copy()


M = TypeVar('M', bound=Model)


class TypedModel(Model, Generic[M]):
    DoesNotExist: peewee.DoesNotExist

    @classmethod
    def get_or_create(cls: Type[M], **kwargs) -> Tuple[M, bool]:
        return super().get_or_create(**kwargs)

    @classmethod
    def get(cls: Type[M], *query, **filters) -> M:
        return super().get(*query, **filters)

    @classmethod
    def safe_get(
        cls: Type[M], *query, **filters
    ) -> Result[M, M.DoesNotExist]:
        try:
            return Ok(cls.get(*query, **filters))
        except cls.DoesNotExist as e:
            return Err(e)

    @classmethod
    def batch_insert(cls, row_entries: Iterable[ModelRowFields]):
        rows = [row_entry.to_dict() for row_entry in row_entries]
        return cls.insert_many(rows)


T = TypeVar('T', bound=TypedModel)


class BoundRowFields(ModelRowFields, Generic[T]):
    def __init__(
        self, base_model: Type[T], fields: Dict[peewee.Field, Any]
    ):
        self.__base_model: Type[T] = base_model
        super().__init__(fields, filter_any=True)

    def get_or_create(self) -> Tuple[T, bool]:
        return self.__base_model.get_or_create(**self._fields)

    def create(self) -> T:
        return self.__base_model.create(**self._fields)

    def get(self) -> T:
        return self.__base_model.get(**self._fields)

    def safe_get(self) -> Result[T, T.DoesNotExist]:
        try:
            return Ok(self.__base_model.get(**self._fields))
        except self.__base_model.DoesNotExist as e:
            return Err(e)

    def select(self) -> peewee.ModelSelect:
        def resolve_field(field_name: str):
            return getattr(self.__base_model, field_name)

        if len(self._fields) > 0:
            field_keys = list(self._fields.keys())
            query = True

            for field_key in field_keys:
                query &= resolve_field(field_key) == self._fields[field_key]

            # print('QUERY', query, field_keys, resolve_field(field_keys[0]))
            result = self.__base_model.select().where(query)
        else:
            result = self.__base_model.select()

        return result

    def insert(self):
        return self.__base_model.insert(**self._fields)

