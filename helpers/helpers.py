from enum import Enum
from typing import List, Sequence, TypeVar, Type
from collections import defaultdict


def get_duplicate_nums(nums: Sequence[int]) -> List[int]:
    num_count = defaultdict(int)
    for num in nums:
        num_count[num] += 1

    return [num for num, count in num_count.items() if count > 1]


T = TypeVar('T', bound=Type[Enum])


def check_enum_distinct_values(enum_class: T) -> T:
    """Check that all enum variants have distinct values."""
    values = {}
    duplicates = []

    for name, value in enum_class.__members__.items():
        if value.value in values:
            duplicates.append((name, value.value, values[value.value]))
        else:
            values[value.value] = name

    assert not duplicates, (
        f"Found duplicate values in {enum_class.__name__}: " +
        ", ".join([
            f"'{val}' used by both {orig} and {name}"
            for name, val, orig in duplicates
        ])
    )

    return enum_class