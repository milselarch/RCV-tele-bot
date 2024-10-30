import textwrap

HELP_TEXT = textwrap.dedent("""
   /start - start bot
   /user_details - shows your username and user id
   ——————————————————
   /create_poll @username_1 @username_2 ... @username_n:
   poll title
   poll option 1
   poll option 2
   ...
   poll option m   

   Creates a new poll
   ——————————————————
   /create_group_poll @username_1 @username_2 ... @username_n:
   poll title
   poll option 1
   poll option 2
   ...
   poll option m

   Creates a new poll that chat members can self-register for
   ——————————————————
   /register_user_id {poll_id} {user_id}
   Registers a user by user_id for a poll
   ——————————————————
   /whitelist_chat_registration {poll_id}
   Whitelists the current chat so that chat members can self-register
   for the poll specified by poll_id within the chat group
   ——————————————————
   /blacklist_chat_registration {poll_id}
   Blacklists the current chat so that chat members cannot 
   self-register for the poll specified by poll_id within the chat
   group
   ——————————————————
   /view_poll {poll_id} - shows poll details given poll_id
   ——————————————————
   /vote {poll_id}: {option_1} > {option_2} > ... > {option_n} 
   /vote {poll_id} {option_1} > {option_2} > ... > {option_n} 
   /vote {poll_id} {option_1} {option_2} ... {option_n} 

   Last option can also accept 2 special values, withhold and abstain:
       > Vote withhold if you want to vote for none of the options
       > Vote abstain if you want to remove yourself from the poll 

   Vote for the poll with the specified poll_id
   requires that the user is one of the registered 
   voters of the poll
   ——————————————————
   /poll_results {poll_id}
   Returns poll results if the poll has been closed
   ——————————————————
   /has_voted {poll_id} 
   Tells you if you've voted for the poll with the 
   specified poll_id
   ——————————————————
   /close_poll {poll_id}
   Close the poll with the specified poll_id
   Note that only the creator of the poll is allowed 
   to issue this command to close the poll
   ——————————————————
   /view_votes {poll_id}
   View all the votes entered for the poll 
   with the specified poll_id. 
   This can only be done after the poll has been closed first
   ——————————————————
   /view_voters {poll_id}
   Show which voters have voted and which have not
   ——————————————————
   /about - view miscellaneous information about the bot
   /view_polls - view all polls created by you
   ——————————————————
   /delete_poll {poll_id} - delete poll by poll_id
   Use /delete_poll --force to force delete the poll without 
   confirmation, regardless of whether poll is open or closed
   ——————————————————
   /delete_account
   /delete_account {deletion_token}
   Delete your user account (this cannot be undone)
   ——————————————————
   /help - view commands available to the bot
""")


def generate_delete_text(deletion_token: str):
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
        /delete_account {deletion_token}
    """)
