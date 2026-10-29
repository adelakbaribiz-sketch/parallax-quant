"""Token-bucket rate limiter with exponential backoff for exchange REST/WS
calls. One instance per exchange, sized from ExchangeConfig."""

from __future__ import annotations

import asyncio
import random
import time


class TokenBucket:
    def __init__(self, rate_per_second: float, burst: int) -> None:
        if rate_per_second <= 0 or burst <= 0:
            raise ValueError("rate_per_second and burst must be positive")
        self.rate = rate_per_second
        self.capacity = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                await asyncio.sleep(deficit / self.rate)


class ExponentialBackoff:
    """Backoff for reconnects / retryable errors, with full jitter to avoid
    a thundering-herd reconnect against an exchange that just recovered."""

    def __init__(self, base_seconds: float, max_seconds: float) -> None:
        self.base = base_seconds
        self.max = max_seconds
        self._attempt = 0

    def reset(self) -> None:
        self._attempt = 0

    def next_delay(self) -> float:
        delay = min(self.max, self.base * (2**self._attempt))
        self._attempt += 1
        return random.uniform(0, delay)
