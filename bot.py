import asyncio
import logging
import database
import telegram
import traceback
import textwrap
import yaml
import re

from database import *
from telegram.ext import Updater, CommandHandler

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


def error_logger(update, context):
    """Log Errors caused by Updates."""
    """Log Errors caused by Updates."""
    logger.warning(
        'Update "%s" caused error "%s"',
        update, context.error
    )


def track_errors(func):
    def caller(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(traceback.format_exc())
            raise e

    return caller


class RankedChoiceBot(object):
    def __init__(self, config_path='config.yml'):
        self.config_path = config_path

        self.bot = None
        self.updater = None

    def start_bot(self):
        with open(self.config_path, 'r') as config_file:
            yaml_data = yaml.safe_load(config_file)
            api_key = yaml_data['telegram']['bot_token']
            self.bot = telegram.Bot(token=api_key)

        self.updater = Updater(api_key, use_context=True)

        # Get the dispatcher to register handlers
        dp = self.updater.dispatcher
        # on different commands - answer in Telegram
        self.register_commands(dp, commands_mapping=self.kwargify(
            start=self.start_handler,
            user_details=self.name_id_handler,
            create_poll=self.create_poll,
            view_poll=self.view_poll
        ))

        # log all errors
        dp.add_error_handler(error_logger)
        self.updater.start_polling()

    @track_errors
    def start_handler(self, update, *args):
        # Send a message when the command /start is issued.
        update.message.reply_text('Bot started')

    @track_errors
    def name_id_handler(self, update, *args):
        # returns current user id and username
        # when command /user_details is invoked
        user = update.message.from_user
        update.message.reply_text(textwrap.dedent(f"""
            user id: {user['username']}
            username: {user['id']}
        """))

    @track_errors
    def create_poll(self, update, *args, **kwargs):
        """
        example:
        ---------------------------
        /create_poll @asd @fad:
        what ice cream is the best
        mochi
        potato
        cookies and cream
        chocolate
        """
        creator_user = update.message.from_user
        creator_username = creator_user['username']
        message = update.message
        raw_text = message.text.strip()

        if ':' not in raw_text:
            message.reply_text("poll creation format wrong")
            return False

        split_index = raw_text.index(':')
        # first part of command is all the users that are in the poll
        command_p1 = raw_text[:split_index].strip()
        # second part of command is the poll question + poll options
        command_p2 = raw_text[split_index + 1:].strip()

        lines = command_p2.split('\n')
        poll_question = lines[0].strip().replace('\n', '')
        poll_options = lines[1:]
        poll_options = [
            poll_option.strip().replace('\n', '')
            for poll_option in poll_options
        ]

        # print('COMMAND_P2', lines)

        if ' ' in command_p1:
            command_p1 = command_p1[command_p1.index(' '):].strip()
        else:
            message.reply_text('poll voters not specified!')

        poll_usernames = command_p1.split()
        poll_users = []

        for poll_user in poll_usernames:
            # telegram usernames must be at least 4 characters long
            if poll_user == 'all':
                if message.chat.type != 'group':
                    message.reply_text('can only add all users in a group')
                    return False
                else:
                    message.reply_text(
                        'adding all users in a group is not suppoerted'
                    )
            else:
                if poll_user.startswith('@'):
                    poll_user = poll_user[1:]

                poll_users.append(poll_user)

        new_poll = Polls.create(
            desc=poll_question, creator=creator_username
        )

        new_poll.save()
        new_poll_id = new_poll.id
        poll_option_rows = []
        poll_user_rows = []

        for poll_option in poll_options:
            poll_option_rows.append(self.kwargify(
                poll_id=new_poll_id, option_name=poll_option
            ))

        for poll_user in poll_users:
            poll_user_rows.append(self.kwargify(
                poll_id=new_poll_id, username=poll_user
            ))

        group_id = update.message.chat_id
        chat = Chats.create(
            poll_id=new_poll_id, tele_id=group_id,
            broadcasted=False
        )

        with db.atomic():
            Options.insert_many(poll_option_rows).execute()
            PollVoters.insert_many(poll_user_rows).execute()
            chat.save()

        poll_message = self.generate_poll_info(
            new_poll_id, poll_question, poll_options,
            num_voters=len(poll_users)
        )

        message.reply_text(poll_message)

    @staticmethod
    def generate_poll_info(
        poll_id, poll_question, poll_options,
        num_votes=0, num_voters=0
    ):
        numbered_poll_options = [
            f'{k + 1}. {poll_option}' for k, poll_option
            in enumerate(poll_options)
        ]

        return textwrap.dedent(f"""
            POLL ID: {poll_id}
            POLL QUESTION: 
            {poll_question}
            ——————————————————
            {num_votes} / {num_voters} voted
            ——————————————————
        """) + f'\n'.join(numbered_poll_options)

    @track_errors
    def view_poll(self, update, *args, **kwargs):
        # user = update.message.from_user
        message = update.message
        raw_text = message.text.strip()
        user = update.message.from_user

        if ' ' not in raw_text:
            message.reply_text('no poll id specified')
            return False

        poll_id = None
        raw_poll_id = raw_text[raw_text.index(' '):].strip()

        try:
            poll_id = int(raw_poll_id)
        except ValueError:
            message.reply_text(f'invalid poll id: {poll_id}')
            return False

        try:
            poll = Polls.select().where(Polls.id == poll_id).get()
        except Polls.DoesNotExist:
            message.reply_text('poll does not exist')
            return False

        poll_option_rows = Options.select().where(
            Options.poll_id == poll.id
        ).order_by(Options.id)

        poll_options = [
            poll_option.option_name for poll_option in poll_option_rows
        ]

        print('POLL_OPTIONS', poll_options, poll.id)
        message.reply_text(str(poll_options))

    @staticmethod
    def kwargify(**kwargs):
        return kwargs

    @staticmethod
    def register_commands(
        dispatcher, commands_mapping, wrap_func=lambda func: func
    ):
        for command_name in commands_mapping:
            handler = commands_mapping[command_name]
            wrapped_handler = wrap_func(handler)
            dispatcher.add_handler(CommandHandler(
                command_name, wrapped_handler
            ))


if __name__ == '__main__':
    rcv_bot = RankedChoiceBot()
    rcv_bot.start_bot()
