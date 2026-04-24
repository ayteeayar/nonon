from __future__ import annotations
import asyncio
import time
from collections import defaultdict
from typing import Any, Callable, Coroutine, TypeVar
import discord
import structlog
log: structlog.BoundLogger = structlog.get_logger(__name__)
T = TypeVar('T')

class BucketQueue:

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = asyncio.Lock()
        self._next_reset: float = 0.0

    async def run(self, coro: Coroutine[Any, Any, T]) -> T:
        async with self._lock:
            now = time.monotonic()
            if self._next_reset > now:
                sleep = self._next_reset - now
                log.debug('bucket.pre_sleep', bucket=self.name, sleep=round(sleep, 3))
                await asyncio.sleep(sleep)
            return await coro

    def throttle(self, retry_after: float) -> None:
        self._next_reset = time.monotonic() + retry_after
        log.info('bucket.throttled', bucket=self.name, retry_after=retry_after)

class RateLimiter:
    MAX_RETRIES = 5
    BASE_BACKOFF = 1.0

    def __init__(self) -> None:
        self._buckets: dict[str, BucketQueue] = defaultdict(lambda: BucketQueue('global'))
        self._semaphore = asyncio.Semaphore(50)

    def _bucket(self, key: str) -> BucketQueue:
        if key not in self._buckets:
            self._buckets[key] = BucketQueue(key)
        return self._buckets[key]

    async def call(self, coro_fn: Callable[..., Coroutine[Any, Any, T]], *args: Any, bucket: str='global', **kwargs: Any) -> T:
        q = self._bucket(bucket)
        retries = 0
        while True:
            async with self._semaphore:
                try:
                    return await q.run(coro_fn(*args, **kwargs))
                except discord.HTTPException as exc:
                    if exc.status == 429:
                        retry_after = float(exc.response.headers.get('Retry-After', 1.0))
                        q.throttle(retry_after)
                        log.warning('rate_limit.hit', bucket=bucket, retry_after=retry_after, attempt=retries + 1)
                    elif exc.status >= 500:
                        backoff = self.BASE_BACKOFF * 2 ** retries
                        log.warning('discord.server_error', status=exc.status, backoff=backoff, attempt=retries + 1)
                        await asyncio.sleep(backoff)
                    else:
                        raise
                retries += 1
                if retries >= self.MAX_RETRIES:
                    log.error('rate_limit.max_retries', bucket=bucket)
                    raise RuntimeError(f'Exceeded max retries for bucket {bucket!r}')