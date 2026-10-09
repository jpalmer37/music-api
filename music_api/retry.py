"""Bounded retries for reads; writes bypass this function."""

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, TypeVar

from requests.exceptions import ConnectionError, HTTPError, Timeout
from spotipy.exceptions import SpotifyException

T = TypeVar("T")


def read_call(method: Callable[..., T], *args, **kwargs) -> T:
    for attempt in range(3):
        try:
            return method(*args, **kwargs)
        except (SpotifyException, HTTPError, ConnectionError, Timeout) as exc:
            headers = {}
            if isinstance(exc, SpotifyException):
                if exc.reason == "QUOTA_EXCEEDED" or exc.http_status not in {429, 500, 502, 503, 504}:
                    raise
                headers = exc.headers or {}
            elif isinstance(exc, HTTPError):
                response = exc.response
                if response is None or response.status_code not in {429, 500, 502, 503, 504}:
                    raise
                headers = response.headers
            if attempt == 2:
                raise
            delay = float(2 ** attempt)
            retry_after = headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        delay = max(delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                    except (ValueError, TypeError):
                        pass
            if delay > 60:
                raise
            time.sleep(delay)
    raise AssertionError("Unreachable retry state")
