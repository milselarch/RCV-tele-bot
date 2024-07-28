from __future__ import annotations

import peewee

from peewee import Model
from typing import (
    Dict, Any, Tuple, Type, TypeVar, Generic
)


class ModelRowFields(object):
    def __init__(self, fields: Dict[peewee.Field, Any]):
        self.__fields: Dict[str, Any] = {}

        for field, value in fields.items():
            #  print('FIELD_NAME', field, field.name)
            if isinstance(field, peewee.ForeignKeyField):
                self.__fields[field.name] = value.id
            else:
                self.__fields[field.name] = value

    def to_dict(self) -> Dict[str, Any]:
        return self.__fields.copy()


M = TypeVar('M', bound=Model)


class TypedModel(Model, Generic[M]):
    @classmethod
    def get_or_create(cls: Type[M], **kwargs) -> Tuple[M, bool]:
        return super().get_or_create(**kwargs)


T = TypeVar('T', bound=TypedModel)


class BoundModelRowFields(ModelRowFields, Generic[T]):
    def __init__(
        self, base_model: Type[T], fields: Dict[peewee.Field, Any]
    ):
        self.__base_model: Type[T] = base_model
        super().__init__(fields)

    def get_or_create(self) -> Tuple[T, bool]:
        return self.__base_model.get_or_create(**self.__fields)

    def insert(self):
        return self.__base_model.insert(**self.__fields)