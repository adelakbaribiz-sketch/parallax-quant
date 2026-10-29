import time

import pytest

from parallax_quant.data.ratelimit import ExponentialBackoff, TokenBucket


@pytest.mark.asyncio
async def test_token_bucket_allows_burst_up_to_capacity():
    bucket = TokenBucket(rate_per_second=1, burst=5)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1  # all 5 burst tokens available immediately


@pytest.mark.asyncio
async def test_token_bucket_throttles_beyond_capacity():
    bucket = TokenBucket(rate_per_second=10, burst=2)
    start = time.monotonic()
    for _ in range(3):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    # 3rd request must wait for refill: ~1/10s
    assert elapsed >= 0.05


def test_token_bucket_rejects_invalid_params():
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=0, burst=5)
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=1, burst=0)


def test_exponential_backoff_grows_and_caps():
    backoff = ExponentialBackoff(base_seconds=1.0, max_seconds=10.0)
    delays = [backoff.next_delay() for _ in range(10)]
    # Jittered, but the underlying cap (before jitter) should plateau at max
    assert all(d <= 10.0 for d in delays)
    assert delays[-1] <= 10.0


def test_exponential_backoff_reset():
    backoff = ExponentialBackoff(base_seconds=1.0, max_seconds=100.0)
    for _ in range(5):
        backoff.next_delay()
    backoff.reset()
    # After reset, first delay should be bounded by base again (before jitter, base * 2**0 = base)
    delay = backoff.next_delay()
    assert delay <= 1.0
