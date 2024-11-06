from enum import StrEnum


class Command(StrEnum):
    START = "start"
    USER_DETAILS = "user_details"
    CHAT_DETAILS = "chat_details"
    CREATE_POLL = "create_poll"
    CREATE_GROUP_POLL = "create_group_poll"
    REGISTER_USER_ID = "register_user_id"
    WHITELIST_CHAT_REGISTRATION = "whitelist_chat_registration"
    BLACKLIST_CHAT_REGISTRATION = "blacklist_chat_registration"

    VIEW_POLL = "view_poll"
    VIEW_POLLS = "view_polls"
    VOTE = "vote"
    POLL_RESULTS = "poll_results"
    HAS_VOTED = "has_voted"
    CLOSE_POLL = "close_poll"
    VIEW_VOTES = "view_votes"
    VIEW_VOTERS = "view_voters"
    ABOUT = "about"
    DELETE_POLL = "delete_poll"
    DELETE_ACCOUNT = "delete_account"
    HELP = "help"
    DONE = "done"

    VOTE_ADMIN = "vote_admin"
    CLOSE_POLL_ADMIN = "close_poll_admin"
    UNCLOSE_POLL_ADMIN = "unclose_poll_admin"
    LOOKUP_FROM_USERNAME_ADMIN = "lookup_from_username_admin"
    INSERT_USER_ADMIN = "insert_user_admin"
