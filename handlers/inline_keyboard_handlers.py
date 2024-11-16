import asyncio
import json
import time

import base_api

from base_api import BaseAPI, UserRegistrationStatus, CallbackCommands
from typing import Callable, Optional, Coroutine
from telegram.ext import CallbackContext
from bot_middleware import track_errors
from database import ChatWhitelist, Polls, PollVoters, Users, SubscriptionTiers
from database.db_helpers import UserID
from helpers import constants
from helpers.locks_manager import PollsLockManager
from tele_helpers import ModifiedTeleUpdate, TelegramHelpers
from telegram import User as TeleUser
from json import JSONDecodeError
from database import db


class PollMessageHandlers(object):
    def __init__(self):
        self._poll_locks_manager = PollsLockManager()

    async def register_for_poll(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        poll_id = int(callback_data['poll_id'])
        query = update.callback_query
        message_id = query.message.message_id
        chat_id = query.message.chat_id
        tele_user = query.from_user
        user = update.user

        if not ChatWhitelist.is_whitelisted(poll_id, chat_id):
            await query.answer("Not allowed to register from this chat")
            return False

        registration_status = self._register_voter(
            poll_id=poll_id, user_id=user.get_user_id(),
            username=tele_user.username
        )

        reply_text = BaseAPI.reg_status_to_msg(registration_status, poll_id)
        if registration_status != base_api.UserRegistrationStatus.REGISTERED:
            await query.answer(reply_text)
            return False

        assert registration_status == UserRegistrationStatus.REGISTERED
        poll_info = BaseAPI.unverified_read_poll_info(poll_id=poll_id)
        notification = query.answer(reply_text)
        poll_message_update = TelegramHelpers.update_poll_message(
            poll_info=poll_info, chat_id=chat_id,
            message_id=message_id, context=context,
            poll_locks_manager=self._poll_locks_manager
        )
        await asyncio.gather(notification, poll_message_update)

    @staticmethod
    def _register_voter(
        poll_id: int, user_id: UserID, username: Optional[str]
    ) -> base_api.UserRegistrationStatus:
        """
        Registers a user by using the username whitelist if applicable,
        or by directly creating a PollVoters entry otherwise
        Does NOT validate if the user is allowed to register for the poll
        """
        poll = Polls.get_or_none(Polls.id == poll_id)
        if poll is None:
            return base_api.UserRegistrationStatus.POLL_NOT_FOUND
        elif poll.closed:
            return base_api.UserRegistrationStatus.POLL_CLOSED

        try:
            PollVoters.build_from_fields(
                user_id=user_id, poll_id=poll_id
            ).get()
            return base_api.UserRegistrationStatus.ALREADY_REGISTERED
        except PollVoters.DoesNotExist:
            pass

        try:
            user = Users.build_from_fields(user_id=user_id).get()
        except Users.DoesNotExist:
            return base_api.UserRegistrationStatus.USER_NOT_FOUND

        try:
            subscription_tier = SubscriptionTiers(user.subscription_tier)
        except ValueError:
            return base_api.UserRegistrationStatus.INVALID_SUBSCRIPTION_TIER

        has_empty_whitelist_entry = False
        ignore_voter_limit = subscription_tier != SubscriptionTiers.FREE

        if username is not None:
            assert isinstance(username, str)
            whitelist_entry_result = base_api.BaseAPI.get_whitelist_entry(
                poll_id=poll_id, user_id=user_id, username=username
            )

            # checks if there is a username whitelist entry that is unoccupied
            if whitelist_entry_result.is_ok():
                whitelist_entry = whitelist_entry_result.unwrap()

                if whitelist_entry.username == username:
                    return base_api.UserRegistrationStatus.ALREADY_REGISTERED
                elif whitelist_entry.username is None:
                    has_empty_whitelist_entry = True

        register_from_whitelist = BaseAPI.register_from_username_whitelist

        with db.atomic():
            if has_empty_whitelist_entry:
                """
                Try to register user via the username whitelist
                if there is a unoccupied username whitelist entry
                We verify again that the username whitelist entry is empty
                here because there is a small chance that the whitelist entry
                is set between the last check and acquisition of database lock
                """
                assert isinstance(username, str)
                username_str = username

                whitelist_user_result = BaseAPI.get_whitelist_entry(
                    username=username_str, poll_id=poll_id,
                    user_id=user_id
                )

                if whitelist_user_result.is_ok():
                    whitelist_entry = whitelist_user_result.unwrap()
                    assert (
                        (whitelist_entry.user is None) or
                        (whitelist_entry.user == user_id)
                    )

                    if whitelist_entry.user == user_id:
                        return UserRegistrationStatus.ALREADY_REGISTERED
                    elif whitelist_entry.user is None:
                        # print("POP", poll_id, user_id, username_str)
                        register_result = register_from_whitelist(
                            poll_id=poll_id, user_id=user_id,
                            ignore_voter_limit=ignore_voter_limit,
                            username=username_str
                        )

                        if register_result.is_ok():
                            return UserRegistrationStatus.REGISTERED
                        else:
                            assert register_result.is_err()
                            return register_result.err_value

                    # username whitelist entry assigned to different user_id
                    assert isinstance(whitelist_entry.user, int)
                    assert whitelist_entry.user != user_id

            """
            Register by adding user to PollVoters directly if and only if
            registration via username whitelist didn't happen
            """
            register_result = base_api.BaseAPI.register_user_id(
                poll_id=poll_id, user_id=user_id,
                ignore_voter_limit=ignore_voter_limit
            )
            # print("REGISTER_RESULT", register_result)
            if register_result.is_ok():
                _, newly_registered = register_result.unwrap()
                if newly_registered:
                    return base_api.UserRegistrationStatus.REGISTERED
                else:
                    return base_api.UserRegistrationStatus.ALREADY_REGISTERED
            else:
                assert register_result.is_err()
                return register_result.err_value

    def add_vote_option(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        # TODO: implement this
        raise NotImplementedError


class InlineKeyboardHandlers(object):
    def __init__(self):
        self.poll_locks_manager = PollsLockManager()
        self.poll_message_handlers = PollMessageHandlers()

        self.handlers: dict[
            CallbackCommands,
            Callable[[
                ModifiedTeleUpdate, CallbackContext, dict[str, any]
            ], Coroutine[any, any, bool]]
        ] = {
            CallbackCommands.REGISTER_FOR_POLL:
                self.poll_message_handlers.register_for_poll,
            CallbackCommands.DELETE_POLL: self.delete_poll,
            CallbackCommands.ADD_VOTE_OPTION:
                self.poll_message_handlers.add_vote_option,
        }

    @track_errors
    async def route(self, update: ModifiedTeleUpdate, context: CallbackContext):
        """
        callback method for buttons in chat group messages
        """
        query = update.callback_query
        raw_callback_data = query.data
        tele_user: TeleUser | None = query.from_user

        if tele_user is None:
            return await query.answer("Only users can be registered")
        if raw_callback_data is None:
            return await query.answer("Invalid callback data")

        try:
            callback_data = json.loads(raw_callback_data)
        except JSONDecodeError:
            return await query.answer("Invalid callback data format")

        if 'command' not in callback_data:
            return await query.answer("Callback command unknown")

        try:
            raw_command = callback_data['command']
            command = base_api.CallbackCommands(raw_command)
        except ValueError:
            return await query.answer("Invalid callback command")
        except KeyError:
            return await query.answer("Callback command not specified")

        if command not in self.handlers:
            return await query.answer(f"Command {command} not supported")

        handler = self.handlers[command]
        return await handler(update, context, callback_data)

    @classmethod
    async def delete_poll(
        cls, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        query = update.callback_query
        poll_id = int(callback_data['poll_id'])
        init_stamp = int(callback_data.get('stamp', 0))
        user_entry: Users = update.user
        user_id = user_entry.get_user_id()
        message_id = query.message.message_id
        chat_id = query.message.chat_id

        if time.time() - init_stamp > constants.DELETE_POLL_BUTTON_EXPIRY:
            await query.answer("Delete button has expired")
            return False

        poll_query = (Polls.id == poll_id) & (Polls.creator == user_id)
        # TODO: write test to check that only poll creator can delete poll
        poll = Polls.get_or_none(poll_query)

        if poll is None:
            await query.answer(f"Poll #{poll_id} does not exist")
            return False

        assert isinstance(poll, Polls)
        poll_creator = poll.get_creator()
        is_poll_creator = user_id == poll_creator.get_user_id()

        if not is_poll_creator:
            await query.answer(f"Not creator of poll #{poll_id}")
            return False
        elif not poll.closed:
            await query.answer(f"Poll #{poll_id} must be closed first")
            return False

        Polls.delete().where(poll_query).execute()
        await query.answer(f"Poll #{poll_id} deleted")
        # remove delete button after deletion is complete
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f'Poll #{poll_id} ({poll.desc}) deleted',
            reply_markup=None
        )
        return True
