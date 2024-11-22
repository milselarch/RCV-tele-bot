from enum import StrEnum


class StartGetParams(StrEnum):
    POLL_ID = 'poll_id'
    WHITELIST_POLL_ID = 'whitelist_poll'
    # INC_MAX_VOTERS_INVOICE = 'inc_max_voters'
    # TODO: implement callback for receipt
    RECEIPT = 'receipt'