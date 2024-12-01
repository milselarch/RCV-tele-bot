import asyncio
# import threading

from typing import Dict


class PollUpdateLocks(object):
    """
    async locks for updating the poll info messages
    for chats with the same poll_id
    """
    def __init__(self):
        # maps chat_ids to async locks
        self.lock_map: Dict[int, asyncio.Lock] = {}
        self.lock = asyncio.Lock()
        self._voter_count: int = 0

    @property
    def voter_count(self) -> int:
        return self._voter_count

    async def update_voter_count(self, voter_count: int) -> bool:
        async with self.lock:
            if voter_count > self._voter_count:
                self._voter_count = voter_count
                return True

            return False

    async def has_correct_voter_count(self, voter_count: int) -> bool:
        async with self.lock:
            return voter_count == self._voter_count

    async def get_chat_lock(self, chat_id: int) -> asyncio.Lock:
        async with self.lock:
            if chat_id in self.lock_map:
                return self.lock_map[chat_id]

            lock = asyncio.Lock()
            self.lock_map[chat_id] = lock
            return lock

    async def remove_chat_lock(self, chat_id: int):
        async with self.lock:
            if chat_id in self.lock_map:
                del self.lock_map[chat_id]

    def __len__(self):
        return len(self.lock_map)


class PollsLockManager(object):
    _instance = None
    """
    async locks for updating the poll info messages
    with the latest voter counts
    """
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PollsLockManager, cls).__new__(
                cls, *args, **kwargs
            )
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'poll_locks_map'):
            self.poll_locks_map: Dict[int, PollUpdateLocks] = {}
            self.lock = asyncio.Lock()

    async def get_poll_locks(self, poll_id: int) -> PollUpdateLocks:
        async with self.lock:
            return self.__get_poll_locks(poll_id=poll_id)

    def __get_poll_locks(self, poll_id: int) -> PollUpdateLocks:
        if poll_id not in self.poll_locks_map:
            self.poll_locks_map[poll_id] = PollUpdateLocks()

        return self.poll_locks_map[poll_id]

    async def remove_chat_lock(self, poll_id: int, chat_id: int):
        async with self.lock:
            poll_locks = self.__get_poll_locks(poll_id)
            await poll_locks.remove_chat_lock(chat_id)

            if (len(poll_locks) == 0) and (poll_id in self.poll_locks_map):
                del self.poll_locks_map[poll_id]
                return True

            return False
