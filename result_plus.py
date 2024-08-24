from typing import TypeAlias, Union, TypeVar, Literal

from result import Ok as BaseOk, Err as BaseErr
from typing_extensions import Generic

T = TypeVar("T", covariant=True)  # Success type
E = TypeVar("E", covariant=True)  # Error type


class Wrapped(object, Generic[T]):
    def __init__(self, value: T):
        self.value: T = value

    def unwrap(self) -> T:
        return self.value


class Err(BaseErr[E]):
    hello = False

    def __bool__(self):
        return False

    def is_err(self) -> Literal[True]:
        return True

    @staticmethod
    def resolve() -> None:
        return None


class Ok(BaseOk[T]):
    hello = True

    def __bool__(self):
        return True

    def safe_unwrap(self) -> T:
        return self.unwrap()

    def resolve(self) -> Wrapped[T] | None:
        return Wrapped(self.unwrap())


Result: TypeAlias = Union[Ok[T], Err[E]]
