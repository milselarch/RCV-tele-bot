from __future__ import annotations

import re
import dataclasses

from result import Result, Ok, Err
from telegram.constants import ParseMode

from handlers.checklist import Checklist
from helpers.constants import (
    CHECKLIST_MAX_NAME_LENGTH, CHECKLIST_ITEM_MAX_LENGTH
)
from telegram import Message, User as TeleUser
from telegram.ext import ContextTypes

from database import db, Users, SubscriptionTiers
from database.db_helpers import UserID, BoundRowFields
from database.checklist_models import (
    Checklist as ChecklistModel,
    ChecklistItem as ChecklistItemModel,
)
from handlers.base_handler import BaseMessageHandler
from helpers.message_buillder import MessageBuilder
from helpers.modified_tele_update import ModifiedTeleUpdate
from helpers.strings import READ_SUBSCRIPTION_TIER_FAILED
from tele_helpers import TelegramHelpers


@dataclasses.dataclass
class ChecklistBuilderTemplate(object):
    creator_id: UserID
    name: str = ''
    items: list[str] = dataclasses.field(default_factory=list)
    subscription_tier: SubscriptionTiers = SubscriptionTiers.FREE

    def validate_params(self) -> Result[None, MessageBuilder]:
        error_message = MessageBuilder()
        subscription_tier = self.subscription_tier
        user_id = self.creator_id

        max_checklists = subscription_tier.get_max_checklists()
        if ChecklistModel.count_checklists_created(user_id) >= max_checklists:
            return Err(error_message.add(
                f"You have reached the maximum number of checklists "
                f"({max_checklists})."
            ))

        if not self.name:
            return Err(error_message.add("Name cannot be empty"))
        elif re.search(r"\s", self.name):
            return Err(error_message.add("Name cannot contain whitespace"))
        elif len(self.name) > CHECKLIST_MAX_NAME_LENGTH:
            return Err(error_message.add(
                f"Name cannot be longer than "
                f"{CHECKLIST_MAX_NAME_LENGTH} characters"
            ))

        if not self.items:
            return Err(error_message.add("Checklist items cannot be empty"))

        for item in self.items:
            if not item:
                return Err(error_message.add("Item cannot be empty"))
            elif len(item) > CHECKLIST_ITEM_MAX_LENGTH:
                return Err(error_message.add(
                    f"Item cannot be longer than "
                    f"{CHECKLIST_ITEM_MAX_LENGTH} characters"
                ))

        return Ok(None)

    @classmethod
    def from_raw_params(
        cls, creator: Users, raw_params: str
    ) -> Result[ChecklistBuilderTemplate, MessageBuilder]:
        creator_id = creator.get_user_id()

        subscription_tier_res = creator.get_subscription_tier()
        if subscription_tier_res.is_err():
            return Err(MessageBuilder().add(READ_SUBSCRIPTION_TIER_FAILED))

        subscription_tier = subscription_tier_res.unwrap()
        max_checklists = subscription_tier.get_max_checklists()
        num_created = ChecklistModel.count_checklists_created(creator_id)

        if num_created >= max_checklists:
            return Err(MessageBuilder().add(
                f"You have reached the maximum number of checklists "
                f"({max_checklists})."
            ))

        lines = raw_params.splitlines()
        if len(lines) < 2:
            # TODO: add interactive creation mode if no args supplied
            # TODO: add example template instead
            return Err(MessageBuilder().add(
                "Checklist needs at least a name and a single item."
            ))

        builder: ChecklistBuilderTemplate = cls(creator_id=creator_id)
        builder.name = lines[0].strip()
        builder.items = [line.strip() for line in lines[1:]]
        validate_res = builder.validate_params()

        if validate_res.is_err():
            return Err(validate_res.unwrap_err())

        return Ok(builder)

    def save_to_db(self) -> Result[ChecklistModel, MessageBuilder]:
        validate_res = self.validate_params()
        if validate_res.is_err():
            return Err(validate_res.unwrap_err())

        with db.atomic():
            checklist = ChecklistModel.build_from_fields(
                owner_id=self.creator_id,
                name=self.name
            ).create()

            checklist_item_rows: list[
                BoundRowFields[ChecklistItemModel]
            ] = []

            for item in self.items:
                checklist_item_row = ChecklistItemModel.build_from_fields(
                    checklist_id=checklist.id, name=item
                )
                checklist_item_rows.append(checklist_item_row)

            ChecklistItemModel.batch_insert(checklist_item_rows).execute()
            return Ok(checklist)


class ChecklistCommandHandlers(BaseMessageHandler):


    async def handle_messages(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        raw_payload: str
    ):
        raise NotImplementedError

    @classmethod
    async def create_checklist(
        cls, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        message: Message = update.message
        user_entry: Users = update.user
        # user_id = user_entry.get_user_id()
        raw_checklist_creation_args = TelegramHelpers.read_raw_command_args(
            update, strip=False
        ).rstrip()

        checklist_builder_res = ChecklistBuilderTemplate.from_raw_params(
            creator=user_entry, raw_params=raw_checklist_creation_args
        )
        if checklist_builder_res.is_err():
            err_message = checklist_builder_res.unwrap_err()
            return await message.reply_text(err_message.get_content())

        checklist_builder = checklist_builder_res.unwrap()
        save_checklist_res = checklist_builder.save_to_db()

        if save_checklist_res.is_err():
            err_message = save_checklist_res.unwrap_err()
            return await message.reply_text(err_message.get_content())

        checklist = save_checklist_res.unwrap()
        await message.reply_text(f"Checklist {checklist.name} created")
        return None

    async def view_checklists(
        self, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        message = update.message
        user_entry: Users = update.user
        user_id = user_entry.get_user_id()
        checklists = ChecklistModel.get_owned_checklists(user_id=user_id)

        if not checklists:
            return await message.reply_text("You have no checklists")

        checklist_names = "\n".join([
            checklist.name for checklist in checklists
        ])
        await message.reply_text(
            f"Your checklists:\n{checklist_names}"
        )
        raise NotImplementedError

    @classmethod
    async def view_checklist(
        cls, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        message = update.message
        user_entry: Users = update.user
        checklist_name = update.message.text

        checklist_model_res = ChecklistModel.get_by_name_as_owner(
            checklist_name=checklist_name,
            owner_id=user_entry.get_user_id()
        )
        if checklist_model_res.is_err():
            return await message.reply_text(
                f"Checklist not found: {update.message.text}"
            )

        checklist_model = checklist_model_res.unwrap()
        checklist = Checklist.from_model(checklist_model)
        markdown = checklist.to_markdown()
        return await message.reply_text(
            markdown, parse_mode=ParseMode.MARKDOWN_V2
        )

    async def edit_checklist(
        self, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        raise NotImplementedError

    async def delete_checklist(
        self, update: ModifiedTeleUpdate, _: ContextTypes.DEFAULT_TYPE
    ):
        # TODO: edit checklist item interactively
        #  - change item name
        #  - mark item as completed
        #  - delete item
        #  - insert above
        #  - insert below
        raise NotImplementedError