from abc import ABCMeta, abstractmethod

from telegram.ext import ContextTypes

from tele_helpers import ModifiedTeleUpdate


class BaseMessageHandler(object, metaclass=ABCMeta):
    @abstractmethod
    async def handle_messages(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        raw_payload: str
    ):
        ...
