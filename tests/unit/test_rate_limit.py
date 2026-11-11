import pytest

from app.core.errors import RateLimitedError
from app.core.rate_limit import TokenBucketLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_burst_then_limited() -> None:
    limiter = TokenBucketLimiter(rate_per_second=5, burst=3, clock=Clock())

    for _ in range(3):
        limiter.check(1)
    with pytest.raises(RateLimitedError) as exc:
        limiter.check(1)

    assert exc.value.code == "rate_limited"


def test_tokens_refill_over_time() -> None:
    clock = Clock()
    limiter = TokenBucketLimiter(rate_per_second=5, burst=2, clock=clock)
    limiter.check(1)
    limiter.check(1)

    clock.now = 0.2  # 0.2 с * 5/с = одно действие

    limiter.check(1)
    with pytest.raises(RateLimitedError):
        limiter.check(1)


def test_refill_is_capped_by_burst() -> None:
    clock = Clock()
    limiter = TokenBucketLimiter(rate_per_second=5, burst=2, clock=clock)
    limiter.check(1)

    clock.now = 100.0

    limiter.check(1)
    limiter.check(1)
    with pytest.raises(RateLimitedError):
        limiter.check(1)


def test_users_are_isolated() -> None:
    limiter = TokenBucketLimiter(rate_per_second=5, burst=1, clock=Clock())
    limiter.check(1)

    limiter.check(2)

    with pytest.raises(RateLimitedError):
        limiter.check(1)
