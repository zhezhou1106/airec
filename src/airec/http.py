"""One HTTP client for every source, with polite retries in a single place."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from airec import __version__

USER_AGENT = f"airec/{__version__} (+https://github.com/zhezhou1106/airec)"
RETRYABLE = {429, 500, 502, 503, 504}
MAX_WAIT_SECONDS = 60.0


class Http:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = httpx.Client(
            timeout=45,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )
        self.sleep = sleep

    def request(self, method: str, url: str, *, attempts: int = 3, **kwargs: Any) -> httpx.Response:
        """Send a request, retrying rate limits and outages. Raises on a final failure."""
        for attempt in range(1, attempts + 1):
            final = attempt == attempts
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError:
                if final:
                    raise
                self.sleep(2.0 * attempt)
                continue
            if response.status_code in RETRYABLE and not final:
                self.sleep(_wait_seconds(response, attempt))
                continue
            response.raise_for_status()
            return response
        raise AssertionError("unreachable: the final attempt returns or raises")

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)


def _wait_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return min(float(retry_after), MAX_WAIT_SECONDS)
    return min(5.0 * attempt, MAX_WAIT_SECONDS)
