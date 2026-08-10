from __future__ import annotations

from helpers.constants import (
    CHECKLIST_MAX_TITLE_LENGTH, CHECKLIST_ITEM_MAX_LENGTH
)
from telegram import Message, User as TeleUser
from telegram.ext import ContextTypes

from database import Users
from database.checklist_models import Checklist
from handlers.base_handler import BaseMessageHandler
from helpers.modified_tele_update import ModifiedTeleUpdate
from helpers.strings import READ_SUBSCRIPTION_TIER_FAILED
from tele_helpers import TelegramHelpers


class ChecklistCommandHandlers(BaseMessageHandler):
    async def handle_messages(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        raw_payload: str
    ):
        raise NotImplementedError

    async def create_checklist(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
    ):
        message: Message = update.message
        creator_user: TeleUser | None = message.from_user
        if creator_user is None:
            await message.reply_text("Creator user not specified")
            return False

        creator_tele_id = creator_user.id
        assert isinstance(creator_tele_id, int)
        user_entry: Users = update.user
        user_id = user_entry.get_user_id()
        raw_checklist_creation_args = TelegramHelpers.read_raw_command_args(
            update, strip=False
        ).rstrip()

        # TODO: implement interactive variant
        subscription_tier_res = user_entry.get_subscription_tier()
        if subscription_tier_res.is_err():
            return await message.reply_text(READ_SUBSCRIPTION_TIER_FAILED)

        subscription_tier = subscription_tier_res.unwrap()
        max_checklists = subscription_tier.get_max_checklists()
        if Checklist.count_checklists_created(user_id) >= max_checklists:
            return await message.reply_text(
                f"You have reached the maximum number of checklists "
                f"({max_checklists})."
            )

        lines = raw_checklist_creation_args.splitlines()
        if len(lines) < 2:
            # TODO: send template instead of just error message
            return await message.reply_text(
                "Checklist needs at least a title and a single item."
            )

        title, checklist_items = lines[0], lines[1:]
        title = title.strip()

        if not title:
            return await message.reply_text("Title cannot be empty")
        if len(title) > CHECKLIST_MAX_TITLE_LENGTH:
            return await message.reply_text(
                f"Title cannot be longer than "
                f"{CHECKLIST_MAX_TITLE_LENGTH} characters"
            )

        for checklist_item in checklist_items:
            checklist_item = checklist_item.strip()
            if not checklist_item:
                return await message.reply_text(
                    "Checklist items cannot be empty"
                )
            if len(checklist_item) > CHECKLIST_ITEM_MAX_LENGTH:
                return await message.reply_text(
                    f"Checklist items cannot be longer than "
                    f"{CHECKLIST_ITEM_MAX_LENGTH} characters"
                )

        raise NotImplementedError