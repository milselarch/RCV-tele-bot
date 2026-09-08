from __future__ import annotations

from typing import Final
from pydantic import BaseModel

from helpers.constants import BLANK_ID
from database.checklist_models import (
    Checklist as ChecklistModel,
    ChecklistItem as ChecklistItemModel
)
from helpers.ordering import build_ordering

ITEM_MARKER: Final[str] = "- "
MARKDOWN_TAB: Final[str] = "  "


class Checklist(BaseModel):
    id: int = BLANK_ID

    name: str
    # checklist items that are immediate children of the checklist
    # maps ordering to checklist item
    top_level_items: dict[int, ChecklistItem]

    @classmethod
    def from_model(cls, model: ChecklistModel) -> Checklist:
        checklist_item_models = model.get_items()
        checklist_items: list[ChecklistItem] = [
            ChecklistItem.from_model(item_model)
            for item_model in checklist_item_models
        ]
        # TODO: test that ordering sorting is maintained
        #   amongst items with the same parent_id after build_ordering is called
        checklist_items = sorted(checklist_items, key=lambda i: i.ordering)
        checklist_items = build_ordering(checklist_items).unwrap()

        item_id_map: dict[int, ChecklistItem] = {}
        parent_item_map: dict[int, ChecklistItem] = {}
        top_level_items: dict[int, ChecklistItem] = {}

        for checklist_item in checklist_items:
            item_id = checklist_item.get_own_id()
            ordering = checklist_item.ordering
            item_id_map[item_id] = checklist_item
            parent_ids = checklist_item.get_dependent_ids()

            if not parent_ids:
                top_level_items[ordering] = checklist_item
            else:
                assert len(parent_ids) == 1
                parent_id = parent_ids[0]
                parent_item = parent_item_map[parent_id]
                parent_item_map[item_id] = parent_item
                parent_item.add_child_item(checklist_item)

        return cls(
            id=model.id,
            name=model.name,
            top_level_items=top_level_items
        )

    def to_lines(self, max_depth: int) -> list[str]:
        lines: list[str] = [self.name]

        for ordering in sorted(self.top_level_items.keys()):
            top_level_item = self.top_level_items[ordering]
            item_lines = top_level_item.to_lines(max_depth)

            for item_line in item_lines:
                lines.append(MARKDOWN_TAB + item_line)

        return lines

    def to_markdown(self) -> str:
        lines = self.to_lines(max_depth=0)
        return "\n".join(lines)


class ChecklistItem(BaseModel):
    id: int = BLANK_ID
    parent_id: int = BLANK_ID
    child_items: list[ChecklistItem] = []

    ordering: int
    text: str
    is_checked: bool = False

    @classmethod
    def from_model(cls, model: ChecklistItemModel) -> ChecklistItem:
        return cls(
            id=model.id,
            parent_id=model.parent_item.id,
            ordering=model.ordering,
            text=model.name,
            is_checked=model.checked
        )

    def to_lines(self, max_depth: int) -> list[str]:
        if max_depth < 0:
            return []

        checkbox = "[x]" if self.is_checked else "[ ]"
        top_line = ITEM_MARKER + f"{checkbox} {self.text}"
        nested_lines: list[str] = [top_line]

        if max_depth > 0:
            for child_item in self.child_items:
                child_lines = child_item.to_lines(max_depth - 1)

                for child_line in child_lines:
                    nested_lines.append(MARKDOWN_TAB + child_line)

        elif self.child_items:
            nested_lines.append(
                MARKDOWN_TAB +
                f'{len(self.child_items)} more items not shown...'
            )

        return nested_lines

    def add_child_item(self, child_item: ChecklistItem):
        self.child_items.append(child_item)

    def get_own_id(self) -> int:
        return self.id

    def get_dependent_ids(self) -> list[int]:
        if self.parent_id == BLANK_ID:
            return []

        return [self.parent_id]
