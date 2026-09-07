"""Shared HTTP helpers: a browser-like User-Agent (Polymarket's Gamma API
rejects the default python UA with 403), retries with backoff, and a simple
token-bucket rate limiter per host."""
from __future__ import annotations

import threading
import time
from typing import Any

import httpx

# Polymarket's Gamma API 403s non-browser user agents; ESPN 403s *browser* user
# agents that lack real browser headers. Each client picks the one that works.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
PLAIN_UA = "sportstrades/0.1 (+https://github.com/VV1Git/sportstrades)"


class RateLimiter:
    """Allow at most `rate` calls per second (thread-safe)."""

    def __init__(self, rate: float):
        self.min_interval = 1.0 / rate if rate > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if self._next > now:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = max(self._next, now) + self.min_interval


class Http:
    def __init__(self, base_url: str, rate: float = 8.0, timeout: float = 30.0, retries: int = 3,
                 user_agent: str = UA):
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(rate)
        self.retries = retries
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
        )

    def request(self, method: str, path: str, **kw: Any) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                r = self.client.request(method, url, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"{r.status_code} {r.text[:200]}", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last = e
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and status < 500 and status != 429:
                    raise
                time.sleep(min(8.0, 0.5 * (2 ** attempt)))
        assert last is not None
        raise last

    def get(self, path: str, params: dict | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Any) -> Any:
        return self.request("POST", path, json=json)
