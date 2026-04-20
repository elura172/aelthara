import asyncio
import logging
import time
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_request(
    func: Callable[[], T],
    max_retries: int = 3,
    backoff_base: float = 1.0,
) -> T:
    last_exception: Exception | None = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:  # pragma: no cover - direct passthrough behavior
            last_exception = e
            if attempt < max_retries - 1:
                delay = backoff_base * (2 ** attempt)
                logger.debug(f"Retry {attempt + 1}/{max_retries} after {delay}s: {e}")
                time.sleep(delay)
    if last_exception is not None:
        raise last_exception
    raise RuntimeError("retry_request exhausted without executing")


async def retry_async_request(
    func: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    backoff_base: float = 1.0,
) -> T:
    last_exception: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:  # pragma: no cover - direct passthrough behavior
            last_exception = e
            if attempt < max_retries - 1:
                delay = backoff_base * (2 ** attempt)
                logger.debug(f"Retry {attempt + 1}/{max_retries} after {delay}s: {e}")
                await asyncio.sleep(delay)
    if last_exception is not None:
        raise last_exception
    raise RuntimeError("retry_async_request exhausted without executing")
