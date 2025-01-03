import asyncio
import aioredlock

from enum import IntEnum
from typing import Optional
from aioredlock import Aioredlock


class GetPollWinnerStatus(IntEnum):
    CACHED = 0
    NEWLY_COMPUTED = 1
    COMPUTING = 2
    FAILED = 3


class RedisCacheManager(object):
    _redis_lock_manager: Optional[Aioredlock] = None
    _connections = []

    POLL_WINNER_KEY = "POLL_WINNER"
    POLL_WINNER_LOCK_KEY = "POLL_WINNER_LOCK"
    # CACHE_LOCK_NAME = "REDIS_CACHE_LOCK"
    POLL_CACHE_EXPIRY = 60

    def __init__(self, connections: list[dict[str, str | int]] | None = None):
        self._connections = connections
        if self._redis_lock_manager is None:
            self._redis_lock_manager = self.create_redis_lock_manager(
                connections
            )

    @property
    def redis_lock_manager(self) -> Aioredlock:
        if self._redis_lock_manager is None:
            self._redis_lock_manager = self.create_redis_lock_manager(
                self._connections
            )

        return self._redis_lock_manager

    async def is_locked(self, resource: str):
        return await self.redis_lock_manager.is_locked(resource)

    async def lock(self, lock_key: str):
        return await self.redis_lock_manager.lock(
            lock_key, lock_timeout=self.POLL_CACHE_EXPIRY
        )

    @staticmethod
    async def refresh_lock(
        lock: aioredlock.Lock, interval: float = POLL_CACHE_EXPIRY / 2
    ):
        try:
            while True:
                print('WAIT')
                await asyncio.sleep(interval)
                await lock.extend()
        except asyncio.CancelledError:
            pass

    @staticmethod
    def create_redis_lock_manager(
        connections: list[dict[str, str | int]] | None = None
    ) -> Aioredlock:
        if connections is not None:
            return Aioredlock(connections)
        else:
            return Aioredlock()

    def build_poll_winner_lock_cache_key(self, poll_id: int) -> str:
        assert isinstance(poll_id, int)
        return self._build_cache_key(
            self.__class__.POLL_WINNER_LOCK_KEY, str(poll_id)
        )

    @staticmethod
    def _build_cache_key(header: str, key: str):
        return f"{header}:{key}"
