from pydantic import BaseModel


class Checklist(BaseModel):
    title: str
    items: list[str]


class ChecklistItem(BaseModel):
    text: str
    is_checked: bool = False