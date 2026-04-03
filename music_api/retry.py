"""Retry decorator with exponential backoff for API rate limits."""

import functools
import time
from typing import Tuple, Type


def retry(
    max_attempts: int = 3,
    backoff_base: float = 1.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """Retry a function on failure with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts before raising.
        backoff_base: Base delay in seconds (doubles each retry).
        retryable_exceptions: Tuple of exception types to catch and retry.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except retryable_exceptions as exc:
                    if attempt == max_attempts - 1:
                        raise
                    wait = backoff_base * (2 ** attempt)
                    time.sleep(wait)

        return wrapper

    return decorator
