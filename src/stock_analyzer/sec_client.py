"""Identified, rate-limited SEC JSON access with immutable raw snapshots."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .ledger import utc_text


@dataclass(frozen=True)
class Snapshot:
    payload: dict
    source_url: str
    observed_at_utc: str
    sha256: str
    path: Path
    from_cache: bool


class SecClient:
    """One sequential client; rate budget must be shared across external processes."""

    def __init__(self, user_agent: str, cache_dir: Path, *, requests_per_second: float = 2,
                 retries: int = 3, timeout: float = 30, opener=urlopen,
                 clock=time.monotonic, sleep=time.sleep, now=lambda: datetime.now(timezone.utc)):
        if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", user_agent) or "\n" in user_agent or "\r" in user_agent:
            raise ValueError("SEC User-Agent must identify your application and contact email")
        if not 0 < requests_per_second <= 5:
            raise ValueError("Use a conservative rate between 0 and 5 requests/second")
        if retries < 0 or timeout <= 0:
            raise ValueError("Invalid retry or timeout setting")
        self.user_agent, self.cache_dir = user_agent, Path(cache_dir)
        self.interval, self.retries, self.timeout = 1 / requests_per_second, retries, timeout
        self.opener, self.clock, self.sleep, self.now = opener, clock, sleep, now
        self.last_request = None

    def _throttle(self):
        if self.last_request is not None:
            delay = self.interval - (self.clock() - self.last_request)
            if delay > 0:
                self.sleep(delay)
        self.last_request = self.clock()

    def fetch_json(self, url: str, *, refresh: bool = False) -> Snapshot:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "data.sec.gov" or parsed.query or parsed.fragment:
            raise ValueError("Only plain HTTPS data.sec.gov URLs are permitted")
        key = hashlib.sha256(url.encode()).hexdigest()
        folder = self.cache_dir / key
        pointer = folder / "latest.json"
        if pointer.exists() and not refresh:
            metadata = json.loads(pointer.read_text())
            digest = metadata["sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or metadata["source_url"] != url:
                raise ValueError("Invalid snapshot metadata")
            path = folder / f"{digest}.json"
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("Cached snapshot checksum mismatch")
            return Snapshot(json.loads(raw), url, metadata["observed_at_utc"], digest, path, True)
        for attempt in range(self.retries + 1):
            self._throttle()
            request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    raw = response.read()
                break
            except (HTTPError, URLError) as error:
                if isinstance(error, HTTPError) and error.code not in (429, 500, 502, 503, 504):
                    raise
                if attempt == self.retries:
                    raise
                # Honor bounded numeric Retry-After; unsupported date forms use backoff.
                header = error.headers.get("Retry-After", "") if isinstance(error, HTTPError) and error.headers else ""
                delay = max(2 ** attempt, min(float(header), 120)) if header.isdigit() else 2 ** attempt
                self.sleep(delay)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("SEC JSON must be an object")
        digest = hashlib.sha256(raw).hexdigest()
        observed = utc_text(self.now())
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{digest}.json"
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("Snapshot content conflict")
        if not path.exists():
            path.write_bytes(raw)
        metadata = {"source_url": url, "observed_at_utc": observed, "sha256": digest}
        # Preserve each observation, including refreshes returning identical bytes.
        observation = folder / f"observation-{observed.replace(':', '-')}-{time.time_ns()}.json"
        observation.write_text(json.dumps(metadata, indent=2) + "\n")
        temp = folder / "latest.tmp"
        temp.write_text(json.dumps(metadata, indent=2) + "\n")
        temp.replace(pointer)
        return Snapshot(payload, url, observed, digest, path, False)

