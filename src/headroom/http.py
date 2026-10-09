"""Polite HTTP: one identified user agent, robots.txt, per-host rate limits,
retries with backoff, and an on-disk cache.

Every connector goes through `Fetcher`, so these rules hold everywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from headroom.config import CACHE_DIR, USER_AGENT, settings

log = logging.getLogger(__name__)

# Minimum seconds between requests to the same host. Conservative by default.
HOST_INTERVAL: dict[str, float] = {
    "api.gleif.org": 1.1,  # documented limit: 60 requests per minute
    "api.riksbank.se": 2.0,  # anonymous access is rate-limited
    "gw.api.bolagsverket.se": 1.5,  # slowed further automatically on 429
    "registers.esma.europa.eu": 1.0,
    "firds.esma.europa.eu": 1.0,
    "mfn.se": 3.0,
    "news.cision.com": 3.0,
}
DEFAULT_INTERVAL = 2.0


class RobotsDisallowed(RuntimeError):
    pass


class RetryableStatus(RuntimeError):
    def __init__(self, status: int, retry_after: float | None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _user_agent() -> str:
    contact = settings().http_contact
    return f"{USER_AGENT} {contact}" if contact else USER_AGENT


@dataclass
class CachedResponse:
    url: str
    status: int
    content: bytes
    content_type: str
    fetched_at: datetime
    from_cache: bool

    def json(self) -> Any:
        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, cache_dir: Path | None = None, timeout: float = 60.0):
        self.cache_dir = (cache_dir or CACHE_DIR) / "http"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(
            headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            follow_redirects=True,
        )
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ politeness

    def _wait(self, host: str) -> None:
        interval = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
        with self._lock:
            last = self._last.get(host, 0.0)
            delay = last + interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(f"{base}/robots.txt", timeout=20)
                if r.status_code >= 400 or "text/html" in r.headers.get("content-type", ""):
                    rp = None  # no robots.txt (or an HTML error page): no restrictions
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(USER_AGENT.split("/")[0], url)

    # ------------------------------------------------------------------ cache

    def _key(self, url: str, params: dict | None) -> str:
        raw = url + "?" + json.dumps(params or {}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _read_cache(self, key: str, ttl: timedelta | None) -> CachedResponse | None:
        meta_p, body_p = self.cache_dir / f"{key}.json", self.cache_dir / f"{key}.bin"
        if not (meta_p.exists() and body_p.exists()):
            return None
        meta = json.loads(meta_p.read_text())
        fetched = datetime.fromisoformat(meta["fetched_at"])
        if ttl is not None and datetime.now(UTC) - fetched > ttl:
            return None
        return CachedResponse(
            meta["url"], meta["status"], body_p.read_bytes(), meta["content_type"], fetched, True
        )

    def _write_cache(self, key: str, resp: CachedResponse) -> None:
        (self.cache_dir / f"{key}.bin").write_bytes(resp.content)
        (self.cache_dir / f"{key}.json").write_text(
            json.dumps(
                {
                    "url": resp.url,
                    "status": resp.status,
                    "content_type": resp.content_type,
                    "fetched_at": resp.fetched_at.isoformat(),
                }
            )
        )

    # ------------------------------------------------------------------ requests

    @retry(
        retry=retry_if_exception(lambda e: isinstance(e, RetryableStatus | httpx.TransportError)),
        wait=wait_exponential_jitter(multiplier=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str, params: dict | None, headers: dict | None) -> httpx.Response:
        self._wait(urlsplit(url).netloc)
        r = self.client.get(url, params=params, headers=headers)
        if r.status_code == 429 or r.status_code >= 500:
            ra = r.headers.get("retry-after")
            wait = float(ra) if ra and ra.isdigit() else None
            if wait:
                time.sleep(min(wait, 120))
            raise RetryableStatus(r.status_code, wait)
        return r

    def get(
        self,
        url: str,
        params: dict | None = None,
        *,
        ttl: timedelta | None = timedelta(hours=20),
        headers: dict | None = None,
        check_robots: bool = True,
    ) -> CachedResponse:
        """GET with cache. `ttl=None` means cached forever (immutable documents)."""
        key = self._key(url, params)
        cached = self._read_cache(key, ttl)
        if cached:
            return cached
        if check_robots and not self.allowed(url):
            raise RobotsDisallowed(url)
        r = self._get(url, params, headers)
        r.raise_for_status()
        resp = CachedResponse(
            str(r.url),
            r.status_code,
            r.content,
            r.headers.get("content-type", ""),
            datetime.now(UTC),
            False,
        )
        self._write_cache(key, resp)
        return resp

    def download(self, url: str, dest: Path, *, check_robots: bool = True) -> Path:
        """Stream a large file to disk. Skips the download if `dest` already exists."""
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        if check_robots and not self.allowed(url):
            raise RobotsDisallowed(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        self._wait(urlsplit(url).netloc)
        with self.client.stream("GET", url, timeout=600) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        tmp.rename(dest)
        return dest

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
