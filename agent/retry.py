from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable


async def with_retry(
    func: Callable[[], Awaitable[Any]],
    *,
    max_attempts: int = 3,
    base_delay_s: float = 0.1,
    max_jitter_s: float = 0.05,
) -> tuple[Any, int]:
    """Retries transient failures (timeouts, malformed payloads that raise, I/O)."""
    retriable = (
        TimeoutError,
        asyncio.TimeoutError,
        ConnectionError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    )
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await func(), attempt
        except retriable as exc:
            last_error = exc
            if attempt == max_attempts:
                raise
            jitter = random.uniform(0, max_jitter_s)
            await asyncio.sleep(base_delay_s * (2 ** (attempt - 1)) + jitter)
    raise RuntimeError("Retry loop failed unexpectedly.") from last_error
