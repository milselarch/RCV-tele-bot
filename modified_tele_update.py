from database import Users
from telegram import Update as BaseTeleUpdate


class ModifiedTeleUpdate(object):
    def __init__(
        self, update: BaseTeleUpdate, user: Users
    ):
        self.update: BaseTeleUpdate = update
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
