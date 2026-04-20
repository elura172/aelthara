import asyncio
import logging
import time

logger = logging.getLogger(__name__)


def retry_request(func, max_retries: int = 3, backoff_base: float = 1.0):
    """Retry a function with exponential backoff."""
    last_exception = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = backoff_base * (2 ** attempt)
                logger.debug(f"Retry {attempt + 1}/{max_retries} after {delay}s: {e}")
                time.sleep(delay)
    raise last_exception


async def retry_async_request(coro_func, max_retries: int = 3, backoff_base: float = 1.0):
    """Retry an async function with exponential backoff."""
    last_exception = None
    for attempt in range(max_retries):
        try:
            return await coro_func()
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = backoff_base * (2 ** attempt)
                logger.debug(f"Retry {attempt + 1}/{max_retries} after {delay}s: {e}")
                await asyncio.sleep(delay)
    raise last_exception
