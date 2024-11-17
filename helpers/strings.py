import re
import textwrap

from helpers.commands import Command

__VERSION__ = '1.2.1'

POLL_ID_GET_PARAM = 'poll_id'
WHITELIST_POLL_ID_GET_PARAM = 'whitelist_poll'
READ_SUBSCRIPTION_TIER_FAILED = "Unexpected error reading subscription tier"
POLL_OPTIONS_LIMIT_REACHED_TEXT = textwrap.dedent(f"""
    Poll creation limit reached
    Use /{Command.DELETE_POLL} {{POLL_ID}} to remove unused polls
    Use /{Command.VIEW_POLLS} to view created polls
""")

COMMAND_OPTIONS_EXAMPLE = "{option_1} > {option_2} > ... > {option_n}"
HELP_TEXT = textwrap.dedent(f"""
   /{Command.START} - start bot
   /{Command.USER_DETAILS} - shows your username and user id
   ——————————————————
   /{Command.CREATE_POLL} @username_1 @username_2 ... @username_n:
   poll title
   poll option 1
   poll option 2
   ...
   poll option m   

   Creates a new poll
   ——————————————————
   /{Command.CREATE_POLL} @username_1 @username_2 ... @username_n:
   poll title
   poll option 1
   poll option 2
   ...
   poll option m

   Creates a new poll that chat members can self-register for
   ——————————————————
   /{Command.REGISTER_USER_ID} {{poll_id}} {{user_id}}
   Registers a user by user_id for a poll
   ——————————————————
   /{Command.WHITELIST_CHAT_REGISTRATION} {{poll_id}}
   Whitelists the current chat so that chat members can self-register
   for the poll specified by poll_id within the chat group
   ——————————————————
   /{Command.BLACKLIST_CHAT_REGISTRATION} {{poll_id}}
   Blacklists the current chat so that chat members cannot 
   self-register for the poll specified by poll_id within the chat
   group
   ——————————————————
   /{Command.VIEW_POLL} {{poll_id}} - shows poll details given poll_id
   ——————————————————
   /{Command.VOTE} {{poll_id}}: {COMMAND_OPTIONS_EXAMPLE}
   /{Command.VOTE} {{poll_id}} {COMMAND_OPTIONS_EXAMPLE}
   /{Command.VOTE} {{poll_id}} {{option_1}} {{option_2}} ... {{option_n}} 

   Last option can also accept 2 special values, withhold and abstain:
       > Vote withhold if you want to vote for none of the options
       > Vote abstain if you want to remove yourself from the poll 

   Vote for the poll with the specified poll_id
   requires that the user is one of the registered 
   voters of the poll
   ——————————————————
   /{Command.POLL_RESULTS} {{poll_id}}
   Returns poll results if the poll has been closed
   ——————————————————
   /{Command.HAS_VOTED} {{poll_id}} 
   Tells you if you've voted for the poll with the 
   specified poll_id
   ——————————————————
   /{Command.CLOSE_POLL} {{poll_id}}
   Close the poll with the specified poll_id
   Note that only the creator of the poll is allowed 
   to issue this command to close the poll
   ——————————————————
   /{Command.VIEW_VOTES} {{poll_id}}
   View all the votes entered for the poll 
   with the specified poll_id. 
   This can only be done after the poll has been closed first
   ——————————————————
   /{Command.VIEW_VOTERS} {{poll_id}}
   Show which voters have voted and which have not
   ——————————————————
   /{Command.ABOUT} - view miscellaneous information about the bot
   /{Command.VIEW_POLLS} - view all polls created by you
   ——————————————————
   /{Command.DELETE_POLL} {{poll_id}} - delete poll by poll_id
   Use /{Command.DELETE_POLL} --force to force delete the poll without 
   confirmation, regardless of whether poll is open or closed
   ——————————————————
   /{Command.DELETE_ACCOUNT}
   /{Command.DELETE_ACCOUNT} {{deletion_token}}
   Delete your user account (this cannot be undone)
   ——————————————————
   /{Command.HELP} - view commands available to the bot
""")


def generate_delete_text(deletion_token: str) -> str:
    return textwrap.dedent(f"""
        Deleting your account will accomplish the following:
        - all polls you've created will be deleted
        - all votes you've cast for any ongoing polls
          will be deleted, and you will be deregistered
          from these ongoing polls
        - all votes you've cast for any closed polls will
          be decoupled from your user account
        - your user account will be marked as deleted and you
          will not be able to create new polls or vote using
          your account moving forward
        - your user account will be removed from the database
          28 days after being marked for deletion 
        
        Confirm account deletion by running the delete command 
        with the provided deletion token:
        ——————————————————
        /{Command.DELETE_ACCOUNT} {deletion_token}
    """)


def escape_markdown(string: str) -> str:
    # https://stackoverflow.com/questions/40626896/
    return re.sub(
        r'[_*[\]()~>#+\-=|{}.!]', lambda x: '\\' + x.group(),
        string
    )


def generate_vote_option_prompt(rank: int) -> str:
    if rank == 1:
        return f"Enter the poll option you want to rank #{rank}:"
    else:
        return (
            f"Enter the poll option you want to rank #{rank}, "
            f"or use /done if you're done:"
        )


def generate_poll_closed_message(poll_id: int):
    return f"Poll #{poll_id} has been closed already"
