import asyncio
import json
import logging
import time

import telegram
import base_api

from abc import ABCMeta, abstractmethod
from base_api import BaseAPI, UserRegistrationStatus, CallbackCommands
from telegram._utils.types import ReplyMarkup
from typing import Optional, Type
from telegram.ext import CallbackContext
from bot_middleware import track_errors
from database.db_helpers import UserID
from helpers import constants, strings
from helpers.chat_contexts import VoteChatContext
from helpers.locks_manager import PollsLockManager
from helpers.strings import generate_poll_closed_message, generate_poll_deleted_message
from tele_helpers import ModifiedTeleUpdate, TelegramHelpers
from telegram import User as TeleUser, Message
from json import JSONDecodeError

from database import db
from database import (
    ChatWhitelist, Polls, PollVoters, Users, SubscriptionTiers
)
from helpers.message_contexts import (
    VoteMessageContext, extract_message_context, ExtractMessageContextErrors
)


async def register_for_poll(
    update: ModifiedTeleUpdate, context: CallbackContext,
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

    registration_status = _register_voter(
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
        poll_locks_manager=PollsLockManager()
    )
    await asyncio.gather(notification, poll_message_update)


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


class BaseMessageHandler(object, metaclass=ABCMeta):
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    @abstractmethod
    async def handle_queries(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        ...


class RegisterPollMessageHandler(BaseMessageHandler):
    async def handle_queries(
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

        registration_status = _register_voter(
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
            poll_locks_manager=PollsLockManager()
        )
        await asyncio.gather(notification, poll_message_update)


class DeletePollMessageHandler(BaseMessageHandler):
    async def handle_queries(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        query = update.callback_query
        poll_id = int(callback_data['poll_id'])
        init_stamp = int(callback_data.get('stamp', 0))
        user_entry: Users = update.user
        user_id = user_entry.get_user_id()
        message_id = query.message.message_id
        user_tele_id = query.message.from_user.id
        chat_id = query.message.chat_id

        if time.time() - init_stamp > constants.DELETE_POLL_BUTTON_EXPIRY:
            await query.answer("Delete button has expired")
            return False

        poll_query = (Polls.id == poll_id) & (Polls.creator == user_id)
        # TODO: write test to check that only poll creator can delete poll
        poll = Polls.get_or_none(poll_query)

        if poll is None:
            return await query.answer(f"Poll #{poll_id} does not exist")

        assert isinstance(poll, Polls)
        poll_creator = poll.get_creator()
        is_poll_creator = user_id == poll_creator.get_user_id()

        if not is_poll_creator:
            return await query.answer(f"Not creator of poll #{poll_id}")
        elif not poll.closed:
            return await query.answer(f"Poll #{poll_id} must be closed first")

        delete_comment = f"Poll #{poll_id} user#{user_id} tele#{user_tele_id}"
        self.logger.warning(f"Deleting {delete_comment}")
        Polls.delete().where(poll_query).execute()
        self.logger.warning(f"Deleted {delete_comment}")
        await query.answer(f"Poll #{poll_id} deleted")
        # remove delete button after deletion is complete
        return await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f'Poll #{poll_id} ({poll.desc}) deleted',
            reply_markup=None
        )


class AddVoteMessageHandler(BaseMessageHandler):
    async def handle_queries(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        query = update.callback_query
        message_id = query.message.message_id
        user = update.user

        extracted_message_context_res = extract_message_context(update)
        poll_id = int(callback_data['poll_id'])
        ranked_option = int(callback_data['option'])
        poll_closed_res = Polls.get_is_closed(poll_id)

        if poll_closed_res.is_err():
            return await query.answer(generate_poll_deleted_message(poll_id))
        elif poll_closed_res.unwrap():
            return await query.answer(generate_poll_closed_message(poll_id))

        if extracted_message_context_res.is_err():
            error = extracted_message_context_res.unwrap_err()
            if error == ExtractMessageContextErrors.LOAD_FAILED:
                return await query.answer("Failed to load context")

            poll_info = BaseAPI.unverified_read_poll_info(poll_id=poll_id)
            vote_context = VoteMessageContext(
                message_id=message_id, poll_id=poll_id,
                max_options=poll_info.max_options,
                user_id=user.get_user_id()
            )
        else:
            extracted_message_context = extracted_message_context_res.unwrap()
            vote_context_res = VoteMessageContext.load(
                extracted_message_context.message_context
            )
            if vote_context_res.is_err():
                return await query.answer("Failed to load context")

            vote_context = vote_context_res.unwrap()

        add_ranked_option_res = vote_context.add_option(ranked_option)
        # print('ADD_OPTIONS', ranked_option, add_ranked_option_res)
        if add_ranked_option_res.is_err():
            error = add_ranked_option_res.unwrap_err()
            return await query.answer(str(error))

        vote_context.save_state()
        # print('CURRENT_RANKINGS', vote_context.rankings)
        return await query.answer(
            f'Current vote: {vote_context.rankings_to_str()}'
        )


class UndoVoteRankingMessageHandler(BaseMessageHandler):
    async def handle_queries(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        query = update.callback_query
        extracted_message_context_res = extract_message_context(update)
        if extracted_message_context_res.is_err():
            return await query.answer("Vote is empty")

        extracted_message_context = extracted_message_context_res.unwrap()
        vote_context_res = VoteMessageContext.load(
            extracted_message_context.message_context
        )
        if vote_context_res.is_err():
            return await query.answer("Failed to load context")

        vote_context = vote_context_res.unwrap()
        num_vote_rankings = vote_context.pop()
        assert num_vote_rankings >= 0

        poll_id = vote_context.poll_id
        poll_closed_res = Polls.get_is_closed(poll_id)

        if poll_closed_res.is_err():
            return await query.answer(generate_poll_deleted_message(poll_id))
        elif poll_closed_res.unwrap():
            return await query.answer(generate_poll_closed_message(poll_id))

        if num_vote_rankings == 0:
            extracted_message_context.message_context.delete_instance()
            return await query.answer("Vote is now empty")
        else:
            vote_context.save_state()
            return await query.answer(
                f'Current vote: {vote_context.rankings_to_str()}'
            )


class ResetVoteMessageHandler(BaseMessageHandler):
    async def handle_queries(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        query = update.callback_query
        extracted_message_context_res = extract_message_context(update)
        if extracted_message_context_res.is_err():
            return await query.answer("Vote was empty")

        extracted_message_context = extracted_message_context_res.unwrap()
        vote_context_res = VoteMessageContext.load(
            extracted_message_context.message_context
        )
        vote_context = vote_context_res.unwrap()
        poll_id = vote_context.poll_id
        poll_closed_res = Polls.get_is_closed(poll_id)

        if poll_closed_res.is_err():
            return await query.answer(generate_poll_deleted_message(poll_id))
        elif poll_closed_res.unwrap():
            return await query.answer(generate_poll_closed_message(poll_id))
        else:
            extracted_message_context.message_context.delete_instance()
            return await query.answer("Vote is now empty")


class ViewVoteMessageHandler(BaseMessageHandler):
    async def handle_queries(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        query = update.callback_query
        extracted_message_context_res = extract_message_context(update)
        poll_id = int(callback_data['poll_id'])

        if extracted_message_context_res.is_err():
            has_voted = BaseAPI.check_has_voted(
                poll_id=poll_id, user_id=update.user.id
            )
            if has_voted:
                return await query.answer("Vote is empty (submitted already)")
            else:
                return await query.answer("Vote is empty")

        extracted_message_context = extracted_message_context_res.unwrap()
        vote_context_res = VoteMessageContext.load(
            extracted_message_context.message_context
        )
        vote_context = vote_context_res.unwrap()
        poll_id = vote_context.poll_id
        poll_closed_res = Polls.get_is_closed(poll_id)

        if poll_closed_res.is_err():
            return await query.answer(generate_poll_deleted_message(poll_id))
        elif poll_closed_res.unwrap():
            return await query.answer(generate_poll_closed_message(poll_id))

        if vote_context_res.is_err():
            return await query.answer("Failed to load context")

        vote_context = vote_context_res.unwrap()
        return await query.answer(
            f'Current vote: {vote_context.rankings_to_str()}'
        )


class SubmitVoteMessageHandler(BaseMessageHandler):
    async def handle_queries(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        query = update.callback_query
        message: Message = query.message
        tele_user: TeleUser = query.from_user
        chat_id = message.chat_id
        message_id = query.message.message_id

        poll_id = int(callback_data['poll_id'])
        poll_closed_res = Polls.get_is_closed(poll_id)

        if poll_closed_res.is_err():
            return await query.answer(generate_poll_deleted_message(poll_id))
        elif poll_closed_res.unwrap():
            return await query.answer(generate_poll_closed_message(poll_id))

        extracted_message_context_res = extract_message_context(update)
        if extracted_message_context_res.is_err():
            # message chat context is empty
            # (i.e. number buttons weren't pressed)
            has_voted = BaseAPI.check_has_voted(
                poll_id=poll_id, user_id=update.user.id
            )
            if has_voted:
                return await query.answer("Vote is empty (submitted already)")
            else:
                return await query.answer("Vote is empty")

        extracted_message_context = extracted_message_context_res.unwrap()
        vote_context_res = VoteMessageContext.load(
            extracted_message_context.message_context
        )
        if vote_context_res.is_err():
            return await query.answer("Failed to load context")

        vote_context = vote_context_res.unwrap()
        # print('TELE_USER_ID:', tele_user.id)
        register_vote_result = BaseAPI.register_vote(
            chat_id=chat_id, rankings=vote_context.rankings,
            poll_id=vote_context.poll_id,
            username=tele_user.username, user_tele_id=tele_user.id
        )

        if register_vote_result.is_err():
            error_message = register_vote_result.unwrap_err()
            return await error_message.call(query.answer)

        # whether the voter was registered for the poll during the vote itself
        is_first_vote, newly_registered = register_vote_result.unwrap()
        extracted_message_context.message_context.delete_instance()
        await query.answer("Vote Submitted")

        if is_first_vote or newly_registered:
            poll_info = BaseAPI.unverified_read_poll_info(poll_id=poll_id)
            await TelegramHelpers.update_poll_message(
                poll_info=poll_info, chat_id=chat_id,
                message_id=message_id, context=context,
                poll_locks_manager=PollsLockManager()
            )


class RegisterSubmitMessageHandler(BaseMessageHandler):
    async def handle_queries(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        user = update.user
        user_id = user.get_user_id()
        query = update.callback_query
        message: Message = query.message
        tele_user: TeleUser = query.from_user
        message_id = query.message.message_id
        poll_id = int(callback_data['poll_id'])
        poll_closed_res = Polls.get_is_closed(poll_id)
        chat_id = message.chat_id
        coroutines = []

        if poll_closed_res.is_err():
            return await query.answer(generate_poll_deleted_message(poll_id))
        elif poll_closed_res.unwrap():
            return await query.answer(generate_poll_closed_message(poll_id))

        extracted_message_context_res = extract_message_context(update)
        poll_voter_res = PollVoters.get_poll_voter(
            poll_id=poll_id, user_id=user_id
        )
        registered = poll_voter_res.is_ok()
        has_message_context = extracted_message_context_res.is_ok()

        if has_message_context:
            # message context vote info exists,
            # therefore we just submit the vote in the message vote context
            extracted_message_context = extracted_message_context_res.unwrap()
            vote_context_res = VoteMessageContext.load(
                extracted_message_context.message_context
            )
            if vote_context_res.is_err():
                return await query.answer("Failed to load context")

            vote_context = vote_context_res.unwrap()
            # print('TELE_USER_ID:', tele_user.id)
            register_vote_result = BaseAPI.register_vote(
                chat_id=chat_id, rankings=vote_context.rankings,
                poll_id=vote_context.poll_id,
                username=tele_user.username, user_tele_id=tele_user.id
            )

            if register_vote_result.is_err():
                error_message = register_vote_result.unwrap_err()
                return await error_message.call(query.answer)

            # whether the voter was registered for the poll during the vote itself
            _, newly_registered = register_vote_result.unwrap()
            extracted_message_context.message_context.delete_instance()

            if newly_registered:
                poll_info = BaseAPI.unverified_read_poll_info(poll_id=poll_id)
                await TelegramHelpers.update_poll_message(
                    poll_info=poll_info, chat_id=chat_id,
                    message_id=message_id, context=context,
                    poll_locks_manager=PollsLockManager()
                )

            return await query.answer("Vote Submitted")

        assert not has_message_context
        newly_registered = False

        if not registered:
            # not registered, no message context vote found
            if not ChatWhitelist.is_whitelisted(poll_id, chat_id):
                return await query.answer(
                    "Not allowed to register from this chat"
                )

            register_status = _register_voter(
                poll_id=poll_id, user_id=user_id,
                username=tele_user.username
            )
            if register_status == UserRegistrationStatus.REGISTERED:
                newly_registered = True
                poll_info = BaseAPI.unverified_read_poll_info(poll_id=poll_id)
                coroutines.append(TelegramHelpers.update_poll_message(
                    poll_info=poll_info, chat_id=chat_id,
                    message_id=message_id, context=context,
                    poll_locks_manager=PollsLockManager()
                ))
            else:
                return await query.answer(BaseAPI.reg_status_to_msg(
                    register_status, poll_id
                ))

        # create vote chat DM context and try to send a message to the user
        poll_info_res = BaseAPI.read_poll_info(
            poll_id=poll_id, user_id=user_id,
            username=tele_user.username, chat_id=message.chat_id
        )
        if poll_info_res.is_err():
            error_message = poll_info_res.err()
            return await error_message.call(query.answer)

        poll_info = poll_info_res.unwrap()
        vote_context = VoteChatContext(
            user_id=user_id, chat_id=tele_user.id,
            max_options=poll_info.max_options, poll_id=poll_id
        )
        vote_context.save_state()
        bot_username = context.bot.username

        async def send_dm(text, markup: Optional[ReplyMarkup] = None):
            await context.bot.send_message(
                text=text, chat_id=tele_user.id, reply_markup=markup
            )

        if newly_registered:
            resp_header = "Registered for poll"
        else:
            resp_header = "Registered already"

        try:
            # check that we can send a message to user directly
            # i.e. check that bot DM with user has been opened
            await send_dm(strings.BOT_STARTED)
            # raise telegram.error.BadRequest("")
        except telegram.error.BadRequest:
            resp = f"{resp_header} - start the bot to cast your vote"
            return await query.answer(resp)

        resp = f"{resp_header} - poll info has been sent via DM"
        coroutine = query.answer(resp)
        coroutines.append(coroutine)

        poll_message = BaseAPI.generate_poll_message(
            poll_info=poll_info, bot_username=bot_username,
            add_instructions=False
        )
        poll = poll_message.poll_info.metadata
        reply_markup = BaseAPI.generate_vote_markup(
            tele_user=tele_user, poll_id=poll_id, chat_type='private',
            open_registration=poll.open_registration,
            num_options=poll_message.poll_info.max_options
        )
        # display poll info in chat DMs at the start
        poll_contents = poll_message.text

        async def dm_poll_info():
            await send_dm(poll_contents, markup=reply_markup)
            await send_dm(vote_context.generate_vote_option_prompt())

        coroutines.append(dm_poll_info())
        await asyncio.gather(*coroutines)


class VoteDirectChatMessageHandler(BaseMessageHandler):
    async def handle_queries(
        self, update: ModifiedTeleUpdate, context: CallbackContext,
        callback_data: dict[str, any]
    ):
        user = update.user
        user_id = user.get_user_id()
        query = update.callback_query
        message: Message = query.message
        tele_user: TeleUser = query.from_user
        message_id = query.message.message_id
        poll_id = int(callback_data['poll_id'])
        poll_closed_res = Polls.get_is_closed(poll_id)
        chat_id = message.chat_id
        coroutines = []

        if poll_closed_res.is_err():
            return await query.answer(generate_poll_deleted_message(poll_id))
        elif _poll_closed := poll_closed_res.unwrap():
            return await query.answer(generate_poll_closed_message(poll_id))

        poll_voter_res = PollVoters.get_poll_voter(poll_id, user_id=user_id)
        registered = poll_voter_res.is_ok()
        newly_registered = False

        if not registered:
            # not registered, no message context vote found
            if not ChatWhitelist.is_whitelisted(poll_id, chat_id):
                return await query.answer(
                    "Not allowed to register from this chat"
                )

            register_status = _register_voter(
                poll_id=poll_id, user_id=user_id,
                username=tele_user.username
            )
            if register_status == UserRegistrationStatus.REGISTERED:
                newly_registered = True
                poll_info = BaseAPI.unverified_read_poll_info(poll_id=poll_id)
                coroutines.append(TelegramHelpers.update_poll_message(
                    poll_info=poll_info, chat_id=chat_id,
                    message_id=message_id, context=context,
                    poll_locks_manager=PollsLockManager()
                ))
            else:
                # TODO: this shouldn't happen
                return await query.answer(BaseAPI.reg_status_to_msg(
                    register_status, poll_id
                ))

        # create vote chat DM context and try to send a message to the user
        poll_info_res = BaseAPI.read_poll_info(
            poll_id=poll_id, user_id=user_id,
            username=tele_user.username, chat_id=message.chat_id
        )
        if poll_info_res.is_err():
            error_message = poll_info_res.err()
            return await error_message.call(query.answer)

        poll_info = poll_info_res.unwrap()
        current_chat_id = query.message.chat_id
        vote_context = VoteChatContext(
            user_id=user_id, chat_id=tele_user.id,
            max_options=poll_info.max_options, poll_id=poll_id,
            ref_message_id=message_id, ref_chat_id=current_chat_id
        )
        vote_context.save_state()
        bot_username = context.bot.username

        async def send_dm(text, markup: Optional[ReplyMarkup] = None):
            await context.bot.send_message(
                text=text, chat_id=tele_user.id, reply_markup=markup
            )

        if newly_registered:
            resp_header = "Registered for poll"
        else:
            resp_header = "Registered already"

        try:
            # check that we can send a message to user directly
            # i.e. check that bot DM with user has been opened
            await send_dm(strings.BOT_STARTED)
            # raise telegram.error.BadRequest("")
        except telegram.error.BadRequest:
            resp = f"{resp_header} - start the bot to cast your vote"
            return await query.answer(resp)

        resp = f"{resp_header} - poll info has been sent via DM"
        coroutine = query.answer(resp)
        coroutines.append(coroutine)

        poll_message = BaseAPI.generate_poll_message(
            poll_info=poll_info, bot_username=bot_username,
            add_instructions=False
        )
        poll = poll_message.poll_info.metadata
        reply_markup = BaseAPI.generate_vote_markup(
            tele_user=tele_user, poll_id=poll_id, chat_type='private',
            open_registration=poll.open_registration,
            num_options=poll_message.poll_info.max_options,
            ref_message_id=message_id, ref_chat_id=current_chat_id
        )
        # display poll info in chat DMs at the start
        poll_contents = poll_message.text

        async def dm_poll_info():
            await send_dm(poll_contents, markup=reply_markup)
            await send_dm(vote_context.generate_vote_option_prompt())

        coroutines.append(dm_poll_info())
        await asyncio.gather(*coroutines)


class InlineKeyboardHandlers(object):
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.poll_locks_manager = PollsLockManager()

        self.handlers: dict[CallbackCommands, Type[BaseMessageHandler]] = {
            CallbackCommands.REGISTER_FOR_POLL: RegisterPollMessageHandler,
            CallbackCommands.DELETE_POLL: DeletePollMessageHandler,
            CallbackCommands.ADD_VOTE_OPTION: AddVoteMessageHandler,
            CallbackCommands.UNDO_OPTION: UndoVoteRankingMessageHandler,
            CallbackCommands.RESET_VOTE: ResetVoteMessageHandler,
            CallbackCommands.VIEW_VOTE: ViewVoteMessageHandler,
            CallbackCommands.SUBMIT_VOTE: SubmitVoteMessageHandler,
            CallbackCommands.REGISTER_OR_SUBMIT: RegisterSubmitMessageHandler,
            CallbackCommands.VOTE_VIA_DM: VoteDirectChatMessageHandler
        }
        for callback_command in CallbackCommands:
            assert callback_command in self.handlers, callback_command

    @track_errors
    async def route(
        self, update: ModifiedTeleUpdate, context: CallbackContext
    ):
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

        message_handler_cls = self.handlers[command]
        message_handler = message_handler_cls(logger=self.logger)
        return await message_handler.handle_queries(
            update=update, context=context, callback_data=callback_data
        )
