"""Ограничение частоты действий пользователя (token bucket в памяти процесса).

Работает, пока бэкенд запущен одним воркером, как и менеджер WS-соединений.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.errors import RateLimitedError


class RateLimiter(Protocol):
    def check(self, user_id: int) -> None:
        """Списывает одно действие или бросает RateLimitedError."""
        ...


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketLimiter:
    def __init__(
        self, rate_per_second: float, burst: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._rate = rate_per_second
        self._burst = burst
        self._clock = clock
        self._buckets: dict[int, _Bucket] = {}

    def check(self, user_id: int) -> None:
        now = self._clock()
        bucket = self._buckets.get(user_id)
        if bucket is None:
            bucket = self._buckets[user_id] = _Bucket(tokens=self._burst, updated_at=now)
        else:
            elapsed = now - bucket.updated_at
            bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
            bucket.updated_at = now
        if bucket.tokens < 1:
            raise RateLimitedError("rate_limited", "Слишком много сообщений, подожди немного")
        bucket.tokens -= 1
