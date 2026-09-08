from result import Result, Ok, Err

from typing import runtime_checkable, Protocol, TypeVar, Collection, Hashable

T = TypeVar("T", bound=Hashable)


@runtime_checkable
class HasDependencies(Protocol[T]):
    def get_own_id(self) -> T:
        ...
    def get_dependent_ids(self) -> list[T]:
        """
        Returns a list of ids of items that this item depends on.
        """
        ...


H = TypeVar('H', bound=HasDependencies)


def build_ordering(
    items: list[H], assume_satisfied: Collection[T] = ()
) -> Result[list[H], None]:
    """
    Given a list of items that have dependencies on each other,
    return an ordering of the items such that all dependencies are satisfied.
    If no such ordering exists (i.e., there is a cycle), return an empty list.

    Items are ordered from least dependent to most dependent,
    i.e., if A depends on B, then B will come before A in the ordering.

    :param items:
    List of items to order
    :param assume_satisfied:
    Collection of item ids that are assumed to already be satisfied
    before the ordering is built.
    This is useful for cases where some items are already completed,
    and we want to order the remaining items.

    :return:
    Result containing the ordered list of items if successful,
    or an Err with None if no valid ordering exists.
    """
    # maps item id to item
    item_map: dict[T, H] = {}
    # maps item id to the set of prerequisite item ids for that item
    prereq_ids_map = {}

    for item in items:
        item_id = item.get_own_id()
        if item_id in item_map:
            raise ValueError(f"Duplicate item id found: {item_id}")

        item_map[item_id] = item
        prereq_ids_map[item_id] = set()

    for item_id in item_map:
        item = item_map[item_id]
        dependent_item_ids = item.get_dependent_ids()

        for dependent_item_id in dependent_item_ids:
            if dependent_item_id in assume_satisfied:
                # pretend that this dependency does not exist
                # because it is already assumed to be satisfied
                continue

            prereq_ids_map[item_id].add(dependent_item_id)

    ordered_items_res = build_ordering_for_prereqs(item_map, prereq_ids_map)
    return ordered_items_res


def build_ordering_for_prereqs(
    item_id_map: dict[T, H], prereq_ids_map: dict[T, set[T]]
) -> Result[list[H], None]:
    """
    :param item_id_map: map from item id to the corresponding item.
    :param prereq_ids_map: map from item id to prereq item ids for item.
    :return: ordered list of items such that all dependencies are satisfied
    """
    ordered_item_ids = []
    ordered_item_ids_set = set()
    level = set()

    for item_id in prereq_ids_map:
        if len(prereq_ids_map[item_id]) == 0:
            level.add(item_id)
            ordered_item_ids_set.add(item_id)
            ordered_item_ids.append(item_id)

    while level:
        next_level_set = set()

        for item_id in prereq_ids_map:
            for done_item_id in level:
                try:
                    prereq_ids_map[item_id].remove(done_item_id)
                except KeyError:
                    continue

                if len(prereq_ids_map[item_id]) != 0:
                    continue

                next_level_set.add(item_id)
                if item_id not in ordered_item_ids_set:
                    ordered_item_ids_set.add(item_id)
                    ordered_item_ids.append(item_id)

        level = next_level_set

    if len(ordered_item_ids_set) == len(item_id_map):
        ordered_items = [item_id_map[item_id] for item_id in ordered_item_ids]
        return Ok(ordered_items)
    else:
        # return an empty set if ordering not possible
        return Err(None)
