import traceback

from telegram import Update
from load_config import SUDO_TELE_ID
from typing import Callable, Awaitable


def track_errors(func):
    def caller(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(traceback.format_exc())
            raise e

    return caller


def admin_only(func: Callable[..., Awaitable]) -> Callable[..., Awaitable]:
    async def caller(self, update: Update, *args, **kwargs):
        message = update.message
        user = message.from_user
        user_id = user.id

        if user_id != SUDO_TELE_ID:
            await message.reply_text('ACCESS DENIED')
            return False

        return await func(self, update, *args, **kwargs)

    return caller