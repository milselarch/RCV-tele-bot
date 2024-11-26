from enum import StrEnum


class Command(StrEnum):
    START = "start"
    USER_DETAILS = "user_details"
    CHAT_DETAILS = "chat_details"
    ABOUT = "about"
    HELP = "help"

    CREATE_PRIVATE_POLL = "create_private_poll"
    CREATE_GROUP_POLL = "create_poll"
    REGISTER_USER_ID = "register_user_id"
    WHITELIST_CHAT_REGISTRATION = "whitelist_chat_registration"
    BLACKLIST_CHAT_REGISTRATION = "blacklist_chat_registration"
    DELETE_POLL = "delete_poll"
    CLOSE_POLL = "close_poll"

    VIEW_POLL = "view_poll"
    VIEW_POLLS = "view_polls"
    VOTE = "vote"
    POLL_RESULTS = "poll_results"
    HAS_VOTED = "has_voted"
    VIEW_VOTES = "view_votes"
    VIEW_VOTERS = "view_voters"
    DELETE_ACCOUNT = "delete_account"
    DONE = "done"

    SET_MAX_VOTERS = "set_max_voters"
    PAY_SUPPORT = "paysupport"

    VOTE_ADMIN = "vote_admin"
    CLOSE_POLL_ADMIN = "close_poll_admin"
    UNCLOSE_POLL_ADMIN = "unclose_poll_admin"
    LOOKUP_FROM_USERNAME_ADMIN = "lookup_from_username_admin"
    INSERT_USER_ADMIN = "insert_user_admin"
    REFUND_ADMIN = "refund_admin"
    ENTER_MAINTENANCE_ADMIN = "enter_maintenance_admin"
    EXIT_MAINTENANCE_ADMIN = "exit_maintenance_admin"
    SEND_MSG_ADMIN = "send_msg_admin"
