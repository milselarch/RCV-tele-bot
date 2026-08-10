import datetime
import os
import re

from typing import Final
from datetime import timedelta
from pathlib import Path

__VERSION__: Final[str] = '1.4.0'

# project root directory
BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH: Final[str] = os.path.join(
    BASE_DIR, 'config', 'config.yml'
)

BLANK_ID: Final[int] = -1
# TODO: refactor these to be part of config
POLL_MAX_OPTIONS: Final[int] = 16
POLL_OPTION_MAX_LENGTH: Final[int] = 100
MAX_POLL_QUESTION_LENGTH: Final[int] = 256

CHECKLIST_ITEM_MAX_LENGTH: Final[int] = POLL_OPTION_MAX_LENGTH
CHECKLIST_MAX_TITLE_LENGTH: Final[int] = MAX_POLL_QUESTION_LENGTH

# how long before the delete poll button expires
DELETE_POLL_BUTTON_EXPIRY: Final[int] = 60
DELETE_USERS_BACKLOG: Final[timedelta] = datetime.timedelta(days=28)
DELETE_CONTEXTS_BACKLOG: Final[timedelta] = datetime.timedelta(hours=2)
RECEIPT_VALIDITY_BACKLOG: Final[timedelta] = datetime.timedelta(hours=24)
POLLING_TASKS_INTERVAL: Final[int] = 600

ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[1-9]\d*$")
MAX_DISPLAY_VOTE_COUNT: Final[int] = 30
MAX_CONCURRENT_UPDATES: Final[int] = 256
MAX_OPTIONS_PER_ROW: Final[int] = 8
