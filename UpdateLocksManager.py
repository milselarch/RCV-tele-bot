import asyncio

from typing import Dict


class PollUpdateLocks(object):
    def __init__(self):
        # maps chat_ids to async locks
        self.lock_map: Dict[int, asyncio.Lock] = {}
        self._voter_count: int = 0

    @property
    def voter_count(self) -> int:
        return self._voter_count

    def update_voter_count(self, voter_count: int) -> bool:
        if voter_count != self._voter_count:
            self._voter_count = voter_count
            return True

        return False

    def has_correct_voter_count(self, voter_count: int) -> bool:
        return voter_count == self._voter_count

    def get_chat_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id in self.lock_map:
            return self.lock_map[chat_id]

        lock = asyncio.Lock()
        self.lock_map[chat_id] = lock
        return lock

    def remove_chat_lock(self, chat_id: int):
        if chat_id in self.lock_map:
            del self.lock_map[chat_id]

    def __len__(self):
        return len(self.lock_map)


class UpdateLocksManager(object):
    def __init__(self):
        self.poll_locks_map: Dict[int, PollUpdateLocks] = {}

    def get_poll_locks(self, poll_id: int) -> PollUpdateLocks:
        if poll_id not in self.poll_locks_map:
            self.poll_locks_map[poll_id] = PollUpdateLocks()

        return self.poll_locks_map[poll_id]

    def remove_chat_lock(self, poll_id: int, chat_id: int):
        poll_locks = self.get_poll_locks(poll_id)
        poll_locks.remove_chat_lock(chat_id)

        if (len(poll_locks) == 0) and (poll_id in self.poll_locks_map):
            del self.poll_locks_map[poll_id]
            return True

        return False