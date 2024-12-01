import datetime
import re

BLANK_ID = -1
POLL_MAX_OPTIONS: int = 16
POLL_OPTION_MAX_LENGTH: int = 100

# how long before the delete poll button expires
DELETE_POLL_BUTTON_EXPIRY = 60
DELETE_USERS_BACKLOG = datetime.timedelta(days=28)
DELETE_CONTEXTS_BACKLOG = datetime.timedelta(hours=2)
RECEIPT_VALIDITY_BACKLOG = datetime.timedelta(hours=24)
POLLING_TASKS_INTERVAL = 600

ID_PATTERN = re.compile(r"^[1-9]\d*$")
MAX_DISPLAY_VOTE_COUNT = 30
MAX_CONCURRENT_UPDATES = 256
MAX_OPTIONS_PER_ROW = 8
