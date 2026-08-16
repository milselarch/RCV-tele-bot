from typing import Dict, Callable, Coroutine

from database import Users
from telegram import Update as BaseTeleUpdate, User as TeleUser
from helpers.commands import Command


class ModifiedTeleUpdate(object):
    def __init__(
        self, update: BaseTeleUpdate, user: Users,
        tele_user: TeleUser
    ):
        self.update: BaseTeleUpdate = update
        self.tele_user: TeleUser = tele_user
        self.user: Users = user

    @property
    def callback_query(self):
        return self.update.callback_query

    @property
    def message(self):
        return self.update.message

    @property
    def effective_message(self):
        return self.update.effective_message

    @property
    def pre_checkout_query(self):
        return self.update.pre_checkout_query

    @property
    def tele_id(self) -> int:
        return self.tele_user.id

    def is_group_chat(self) -> bool:
        message = self.update.message
        if message is None:
            return False

        return message.chat.type != 'private'


CommandsMapping = Dict[
    Command, Callable[[ModifiedTeleUpdate, ...], Coroutine]
]
