import asyncio
import textwrap

from typing import Type, Coroutine
from abc import ABCMeta, abstractmethod
from telegram import Message, User as TeleUser
from telegram.ext import ContextTypes
from base_api import BaseAPI
from bot_middleware import track_errors
from database.db_helpers import UserID
from handlers.payment_handlers import IncMaxVotersChatContext, PaymentHandlers
from helpers.rcv_tally import RCVTally
from helpers.redis_cache_manager import GetPollWinnerStatus
from helpers.start_get_params import StartGetParams
from helpers import strings
from helpers.commands import Command
from helpers.constants import BLANK_ID
from load_config import SUDO_TELE_ID
from tele_helpers import ModifiedTeleUpdate, TelegramHelpers
from handlers.inline_keyboard_handlers import PollsLockManager
from helpers.chat_contexts import (
    PollCreationChatContext, VoteChatContext, ExtractedChatContext,
    extract_chat_context
)
from helpers.strings import (
    READ_SUBSCRIPTION_TIER_FAILED, generate_poll_created_message
)
from database import (
    Users, CallbackContextState, ChatContextStateTypes, Polls, SupportTickets, PollOptions
)


class BaseContextHandler(object, metaclass=ABCMeta):
    @abstractmethod
    async def complete_chat_context(
        self, chat_context: CallbackContextState,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        ...

    @abstractmethod
    async def handle_messages(
        self, extracted_context: ExtractedChatContext,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        is_from_start: bool
    ):
        """
        :param extracted_context:
        :param update:
        :param context:
        :param is_from_start:
        whether the chat just got initiated from the start command
        """
        ...


class PollCreationContextHandler(BaseContextHandler):
    async def handle_messages(
        self, extracted_context: ExtractedChatContext,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        is_from_start: bool
    ):
        message: Message = update.message
        chat_context = extracted_context.chat_context
        message_text = extracted_context.message_text

        poll_creation_context_res = PollCreationChatContext.load(chat_context)
        if poll_creation_context_res.is_err():
            chat_context.delete()
            return await message.reply_text(
                "Unexpected error loading poll creation context"
            )

        poll_creation_context = poll_creation_context_res.unwrap()
        if not poll_creation_context.has_question:
            # set the poll question and prompt for first poll option
            set_res = poll_creation_context.set_question(message.text)
            if set_res.is_err():
                error = set_res.unwrap_err()
                reply_message = str(error)
            else:
                reply_message = "Enter poll option #1:"
        else:
            # add poll option and prompt for more options
            poll_creation_context.add_option(message_text)
            option_no = 1 + poll_creation_context.num_poll_options

            if option_no <= 2:
                reply_message = f"Enter poll option #{option_no}:"
            else:
                reply_message = (
                    f"Enter poll option #{option_no}, "
                    f"or use /done if you're done:"
                )

        poll_creation_context.save_state()
        return await message.reply_text(reply_message)

    async def complete_chat_context(
        self, chat_context: CallbackContextState,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        user_entry: Users = update.user
        message: Message = update.message
        reply_text = message.reply_text
        tele_user: TeleUser | None = message.from_user
        chat_type = message.chat.type
        user_id = user_entry.get_user_id()

        poll_creation_context_res = PollCreationChatContext.load(chat_context)
        if poll_creation_context_res.is_err():
            chat_context.delete()
            return await reply_text(
                "Unexpected error loading poll creation context"
            )

        poll_creation_context = poll_creation_context_res.unwrap()
        subscription_tier_res = user_entry.get_subscription_tier()
        if subscription_tier_res.is_err():
            return await reply_text(READ_SUBSCRIPTION_TIER_FAILED)

        subscription_tier = subscription_tier_res.unwrap()
        poll_creator = poll_creation_context.to_template(
            creator_id=user_id, subscription_tier=subscription_tier
        )

        create_poll_res = poll_creator.save_poll_to_db()
        if create_poll_res.is_err():
            error_message = create_poll_res.err()
            return await error_message.call(reply_text)

        new_poll: Polls = create_poll_res.unwrap()
        poll_id = int(new_poll.id)
        # self-destruct context once processed
        chat_context.delete_instance()

        view_poll_result = BaseAPI.get_poll_message(
            poll_id=poll_id, user_id=user_id,
            bot_username=context.bot.username,
            username=user_entry.username,
            # set to false here to discourage sending webapp
            # link before group chat has been whitelisted
            add_webapp_link=False,
            add_instructions=update.is_group_chat()
        )
        if view_poll_result.is_err():
            error_message = view_poll_result.err()
            return await error_message.call(reply_text)

        poll_message = view_poll_result.unwrap()
        reply_markup = BaseAPI.generate_vote_markup(
            tele_user=tele_user, poll_id=poll_id,
            chat_type=chat_type, open_registration=True,
            num_options=poll_message.poll_info.max_options
        )

        bot_username = context.bot.username
        deep_link_url = (
            f'https://t.me/{bot_username}?startgroup='
            f'{StartGetParams.WHITELIST_POLL_ID}={poll_id}'
        )
        escaped_deep_link_url = strings.escape_markdown(deep_link_url)

        await reply_text(poll_message.text, reply_markup=reply_markup)
        group_chat_text = (
            "in the group chat of your choice to allow chat members "
            "to register and vote for the poll"
        )
        share_link_text = (
            "Alternatively, click the following link to share the "
            "poll to the group chat of your choice:"
        )
        # https://stackoverflow.com/questions/76538913/
        return await message.reply_markdown_v2(
            strings.escape_markdown(generate_poll_created_message(poll_id)) +
            f'\n\n' +
            f'Run the following command:\n'  
            f"`/{Command.WHITELIST_CHAT_REGISTRATION} {poll_id}` " 
            f"{group_chat_text}\\.\n" +
            f'\n' +
            share_link_text +
            f" [{escaped_deep_link_url}]({escaped_deep_link_url})"
        )


class VoteContextHandler(BaseContextHandler):
    async def handle_messages(
        self, extracted_context: ExtractedChatContext,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        is_from_start: bool
    ):
        message: Message = update.message
        chat_context = extracted_context.chat_context
        message_text = extracted_context.message_text
        vote_context_res = VoteChatContext.load(chat_context)

        if vote_context_res.is_err():
            chat_context.delete()
            return await message.reply_text(
                "Unexpected error loading vote context"
            )

        user = update.user
        tele_user: TeleUser = update.message.from_user
        bot_username = context.bot.username
        vote_context = vote_context_res.unwrap()
        ref_message_id = vote_context.ref_message_id
        ref_chat_id = vote_context.ref_chat_id

        if is_from_start:
            """
            if called from /start command, we send all the information
            about the poll in the chat context and prompt them
            to choose poll options interactively
            """
            if not vote_context.has_poll_id:
                return await message.reply_text("Invalid poll ID")

            poll_id = vote_context.poll_id
            poll_info_res = BaseAPI.read_poll_info(
                poll_id=poll_id, user_id=user.get_user_id(),
                username=tele_user.username, chat_id=message.chat_id
            )
            if poll_info_res.is_err():
                error_message = poll_info_res.err()
                return await error_message.call(message.reply_text)

            poll_info = poll_info_res.unwrap()
            poll_message = BaseAPI.generate_poll_message(
                poll_info=poll_info, bot_username=bot_username
            )
            poll = poll_message.poll_info.metadata
            reply_markup = BaseAPI.generate_vote_markup(
                tele_user=tele_user, poll_id=poll_id, chat_type='private',
                open_registration=poll.open_registration,
                num_options=poll_message.poll_info.max_options,
                ref_message_id=ref_message_id, ref_chat_id=ref_chat_id
            )
            poll_contents = poll_message.text
            await message.reply_text(poll_contents, reply_markup=reply_markup)
            prompt = vote_context.generate_vote_option_prompt()
            return await message.reply_text(prompt)

        if not vote_context.has_poll_id:
            # accept the current text message as the poll_id and set it
            try:
                poll_id = int(message.text)
            except ValueError:
                return await message.reply_text("Invalid poll ID")

            poll_info_res = BaseAPI.read_poll_info(
                poll_id=poll_id, user_id=user.get_user_id(),
                username=tele_user.username, chat_id=message.chat_id
            )

            if poll_info_res.is_err():
                error_message = poll_info_res.err()
                return await error_message.call(message.reply_text)

            poll_info = poll_info_res.unwrap()
            vote_context.set_max_options(poll_info.max_options)
            set_poll_id_res = vote_context.set_poll_id(poll_id)
            if set_poll_id_res.is_err():
                return await message.reply_text(str(
                    set_poll_id_res.unwrap_err()
                ))

            vote_context.save_state()
            return await message.reply_text(
                vote_context.generate_vote_option_prompt()
            )
        else:
            ranked_option_res = BaseAPI.parse_ranked_option(message_text)
            if ranked_option_res.is_err():
                error = ranked_option_res.unwrap_err()
                return await message.reply_text(str(error))

            ranked_option = ranked_option_res.unwrap()
            add_ranked_option_res = vote_context.add_option(ranked_option)
            # print('ADD_OPTIONS', ranked_option, add_ranked_option_res)
            if add_ranked_option_res.is_err():
                error = add_ranked_option_res.unwrap_err()
                return await message.reply_text(str(error))

            vote_context.save_state()
            # print('CURRENT_RANKINGS', vote_context.rankings)
            return await message.reply_text(
                vote_context.generate_vote_option_prompt()
            )

    @track_errors
    async def complete_chat_context(
        self, chat_context: CallbackContextState,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        message: Message = update.message
        vote_creation_context_res = VoteChatContext.load(chat_context)
        if vote_creation_context_res.is_err():
            chat_context.delete()
            return await message.reply_text(
                "Unexpected error loading vote creation context"
            )

        tele_user: TeleUser = message.from_user
        vote_creation_context = vote_creation_context_res.unwrap()
        poll_id = vote_creation_context.poll_id
        register_vote_result = BaseAPI.register_vote(
            chat_id=message.chat_id, rankings=vote_creation_context.rankings,
            poll_id=vote_creation_context.poll_id,
            username=tele_user.username, user_tele_id=tele_user.id
        )

        if register_vote_result.is_err():
            error_message = register_vote_result.unwrap_err()
            return await error_message.call(message.reply_text)

        chat_context.delete_instance()
        is_first_vote, newly_registered = register_vote_result.unwrap()
        send_reply_coroutine = TelegramHelpers.send_post_vote_reply(
            message=message, poll_id=poll_id
        )

        coroutines: list[Coroutine] = [send_reply_coroutine]
        ref_message_id = vote_creation_context.ref_message_id
        ref_chat_id = vote_creation_context.ref_chat_id
        poll_info = BaseAPI.unverified_read_poll_info(poll_id=poll_id)
        update_ref_message = (
            (is_first_vote or newly_registered) and
            (ref_message_id != BLANK_ID)
        )

        if update_ref_message:
            # print('UPDATING_POLL_MESSAGE')
            coroutines.append(TelegramHelpers.update_poll_message(
                poll_info=poll_info, chat_id=ref_chat_id,
                message_id=ref_message_id, context=context,
                poll_locks_manager=PollsLockManager()
            ))

        await asyncio.gather(*coroutines)


class IncreaseMaxVotersContextHandler(BaseContextHandler):
    async def complete_chat_context(
        self, chat_context: CallbackContextState,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        msg: Message = update.message
        extract_context_res = extract_chat_context(update)

        if extract_context_res.is_err():
            error = extract_context_res.unwrap_err()
            return await msg.reply_text(error.to_message())

        extracted_context: ExtractedChatContext = extract_context_res.unwrap()
        chat_context = extracted_context.chat_context
        inc_voters_context_res = IncMaxVotersChatContext.load(chat_context)
        if inc_voters_context_res.is_err():
            chat_context.delete()
            return await msg.reply_text(
                "Unexpected error loading increase max voter context"
            )

        inc_voters_context = inc_voters_context_res.unwrap()
        poll_id = inc_voters_context.get_poll_id()

        if poll_id == BLANK_ID:
            return await msg.reply_text(strings.ENTER_POLL_ID_PROMPT)
        else:
            user_id = update.user.get_user_id()
            poll_res = Polls.get_as_creator(poll_id, user_id)
            if poll_res.is_err():
                return await msg.reply_text(
                    strings.MAX_VOTERS_NOT_EDITABLE
                )

            poll = poll_res.unwrap()
            return await msg.reply_text(strings.generate_max_voters_prompt(
                poll_id, current_max=poll.max_voters
            ))


    async def handle_messages(
        self, extracted_context: ExtractedChatContext,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        is_from_start: bool
    ):
        msg: Message = update.message
        chat_context = extracted_context.chat_context
        message_text = extracted_context.message_text
        user = update.user

        inc_voters_context_res = IncMaxVotersChatContext.load(chat_context)
        if inc_voters_context_res.is_err():
            chat_context.delete()
            return await msg.reply_text(
                "Unexpected error loading increase max voter context"
            )

        inc_voters_context = inc_voters_context_res.unwrap()
        if inc_voters_context.get_poll_id() == BLANK_ID:
            try:
                poll_id = int(message_text)
            except ValueError:
                return await msg.reply_text("Invalid poll ID")

            poll_res = Polls.get_as_creator(poll_id, user.get_user_id())
            if poll_res.is_err():
                return await msg.reply_text(
                    strings.MAX_VOTERS_NOT_EDITABLE
                )

            poll = poll_res.unwrap()
            inc_voters_context.poll_id = poll_id
            inc_voters_context.save_state()
            return await msg.reply_text(strings.generate_max_voters_prompt(
                poll_id, current_max=poll.max_voters
            ))
        else:
            poll_id = inc_voters_context.poll_id
            try:
                new_max_voters = int(message_text)
            except ValueError:
                return await msg.reply_text(
                    "New maximum number of voters must be an integer"
                )

            invoice_sent = await PaymentHandlers.set_max_voters_with_params(
                update=update, context=context, poll_id=poll_id,
                new_max_voters=new_max_voters
            )
            if invoice_sent:
                chat_context.delete_instance()


class PaySupportContextHandler(BaseContextHandler):
    async def complete_chat_context(
        self, chat_context: CallbackContextState,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        return update.message.reply_text(
            "Please enter your payment support ticket details"
        )

    async def handle_messages(
        self, extracted_context: ExtractedChatContext,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        is_from_start: bool
    ):
        chat_context = extracted_context.chat_context
        raw_args = TelegramHelpers.read_raw_command_args(update)

        if len(raw_args) == 0:
            return await update.message.reply_text(
                "Please provide your support ticket details"
            )

        user = update.user
        message = update.message
        user_id = user.get_user_id()
        tele_id = message.from_user.id
        username = message.from_user.username
        support_ticket = SupportTickets.build_from_fields(
            info=message.text, is_payment_support=True
        ).create()

        support_ticket_id = support_ticket.id
        support_message = textwrap.dedent(f"""
            SUPPORT TICKET FROM USER
            {user_id=} {tele_id=} {username=} {support_ticket_id=}
        """) + raw_args

        try:
            await context.bot.send_message(
                chat_id=SUDO_TELE_ID, text=support_message
            )
            await message.reply_text(
                f"Support ticket #{support_ticket_id} has been created"
            )
        finally:
            chat_context.delete_instance()


class ClosePollContextHandler(BaseContextHandler):
    async def handle_messages(
        self, extracted_context: ExtractedChatContext,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        is_from_start: bool
    ):
        message = update.message
        raw_poll_id = message.text
        user_id = update.user.get_user_id()

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            return await message.reply_text(
                f"Invalid poll id: {raw_poll_id}"
            )

        # TODO: implement poll closing here
        chat_context = extracted_context.chat_context
        chat_context.delete_instance()
        await self.close_poll(
            poll_id=poll_id, user_id=user_id,
            update=update
        )

    @staticmethod
    async def close_poll(
        poll_id: int, user_id: UserID, update: ModifiedTeleUpdate
    ):
        message = update.message
        poll_res = Polls.get_as_creator(poll_id, user_id)
        if poll_res.is_err():
            return await message.reply_text(
                "You're not the creator of this poll"
            )

        poll = poll_res.unwrap()
        poll.closed = True
        poll.save()

        await message.reply_text(f'poll {poll_id} closed')
        get_winner_result = await RCVTally().get_poll_winner(poll_id)
        winning_option_id, get_status = get_winner_result

        if get_status == GetPollWinnerStatus.COMPUTING:
            return await message.reply_text(textwrap.dedent(f"""
                Poll winner computation in progress
                Please check again later
            """))
        elif winning_option_id is not None:
            winning_options = PollOptions.select().where(
                PollOptions.id == winning_option_id
            )

            option_name = winning_options[0].option_name
            return await message.reply_text(f'Poll winner is: {option_name}')
        else:
            return await message.reply_text('Poll has no winner')

    async def complete_chat_context(
        self, chat_context: CallbackContextState,
        update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        return await update.message.reply_text(
            f"/{Command.DONE} not supported for closing polls"
        )


class ContextHandlers(object):
    def __init__(self):
        self.context_handlers: dict[
            ChatContextStateTypes, Type[BaseContextHandler]
        ] = {
            ChatContextStateTypes.POLL_CREATION: PollCreationContextHandler,
            ChatContextStateTypes.VOTE: VoteContextHandler,
            ChatContextStateTypes.INCREASE_MAX_VOTERS:
                IncreaseMaxVotersContextHandler,
            ChatContextStateTypes.PAY_SUPPORT: PaySupportContextHandler,
            ChatContextStateTypes.CLOSE_POLL: ClosePollContextHandler
        }

        for context_type in ChatContextStateTypes:
            if context_type not in self.context_handlers:
                raise ValueError(
                    f"Context type {context_type} not implemented"
                )

    @track_errors
    async def handle_other_messages(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE,
        is_from_start: bool = False
    ):
        message: Message = update.message
        chat_context_res = extract_chat_context(update)
        if chat_context_res.is_err():
            error = chat_context_res.unwrap_err()
            return await message.reply_text(error.to_message())

        extracted_context = chat_context_res.unwrap()
        context_type = extracted_context.context_type
        if context_type not in self.context_handlers:
            return await message.reply_text(
                f"{context_type} context unsupported"
            )

        context_handler_cls = self.context_handlers[context_type]
        context_handler = context_handler_cls()
        return await context_handler.handle_messages(
            extracted_context, update, context,
            is_from_start=is_from_start
        )

    @track_errors
    async def complete_chat_context(
        self, update: ModifiedTeleUpdate, context: ContextTypes.DEFAULT_TYPE
    ):
        message: Message = update.message
        extract_context_res = extract_chat_context(update)

        if extract_context_res.is_err():
            error = extract_context_res.unwrap_err()
            return await message.reply_text(error.to_message())

        extracted_context: ExtractedChatContext = extract_context_res.unwrap()
        chat_context: CallbackContextState = extracted_context.chat_context
        context_type = extracted_context.context_type
        if context_type not in self.context_handlers:
            return await message.reply_text(
                f"CONTEXT_NOT_IMPLEMENTED: {chat_context}"
            )

        context_handler_cls = self.context_handlers[context_type]
        context_handler = context_handler_cls()
        return await context_handler.complete_chat_context(
            chat_context, update, context
        )


context_handlers = ContextHandlers()
