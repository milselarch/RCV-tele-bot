import traceback

from telegram import Update
from typing import Callable, Awaitable
from helpers.config_loader import ConfigLoader


def track_errors(func):
    def caller(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(traceback.format_exc())
            raise e

    return caller


def admin_only(func: Callable[..., Awaitable]) -> Callable[..., Awaitable]:
    config = ConfigLoader.load_config()
    sudo_tele_id = config.telegram.sudo_id

    async def caller(self, update: Update, *args, **kwargs):
        message = update.message
        if not message:
            return False

        user = message.from_user
        if user is None:
            return False

        user_id = user.id

        if user_id != sudo_tele_id:
            await message.reply_text('ACCESS DENIED')
            return False

        return await func(self, update, *args, **kwargs)

    return caller
