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
from xml.etree import ElementTree as ET

from .ledger import utc_text
from .xml_safety import strip_external_ownership_doctype


class UnsupportedSourceFormat(ValueError):
    """The SEC URL returned a viewer document instead of raw ownership XML."""


@dataclass(frozen=True)
class Snapshot:
    payload: dict | bytes
    source_url: str
    observed_at_utc: str
    sha256: str
    path: Path
    from_cache: bool


def ownership_archive_url(issuer_cik: str, accession_number: str, primary_document: str) -> str:
    """Build a safe SEC archive URL while preserving nested document paths."""
    cik = str(issuer_cik).strip()
    accession = str(accession_number).strip()
    document = str(primary_document).replace("\\", "/").strip("/")
    if not re.fullmatch(r"[0-9]{1,10}", cik) or int(cik) == 0:
        raise ValueError("Invalid issuer CIK for ownership archive URL")
    if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
        raise ValueError("Invalid accession for ownership archive URL")
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*\.xml",
                        document, flags=re.IGNORECASE):
        raise ValueError("Primary document must be a safe XML archive path")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{document}")


def sec_archive_document_url(issuer_cik: str, accession_number: str, primary_document: str) -> str:
    """Build a safe SEC Archives URL for a primary filing document."""
    cik = str(issuer_cik).strip()
    accession = str(accession_number).strip()
    document = str(primary_document).replace("\\", "/").strip("/")
    if not re.fullmatch(r"[0-9]{1,10}", cik) or int(cik) == 0:
        raise ValueError("Invalid issuer CIK for SEC archive URL")
    if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
        raise ValueError("Invalid accession for SEC archive URL")
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*", document):
        raise ValueError("Primary document must be a safe SEC archive path")
    if Path(document).suffix.lower() not in (".htm", ".html", ".xml", ".txt", ".xhtml"):
        raise ValueError("Unsupported SEC primary document extension")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{document}")

def ownership_submission_url(issuer_cik: str, accession_number: str) -> str:
    """Build the complete-submission text URL for a Form 4 accession."""
    cik = str(issuer_cik).strip()
    accession = str(accession_number).strip()
    if not re.fullmatch(r"[0-9]{1,10}", cik) or int(cik) == 0:
        raise ValueError("Invalid issuer CIK for ownership submission URL")
    if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
        raise ValueError("Invalid accession for ownership submission URL")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}.txt")


def extract_ownership_document(submission: bytes) -> bytes:
    """Extract one raw ownershipDocument from a complete SEC submission."""
    if len(submission) > 50_000_000:
        raise ValueError("SEC complete submission exceeds 50MB limit")
    decoded = submission.decode("utf-8-sig")
    matches = re.findall(
        r"<ownershipDocument\b[^>]*>.*?</ownershipDocument\s*>",
        decoded, flags=re.IGNORECASE | re.DOTALL,
    )
    if len(matches) != 1:
        raise ValueError("Expected exactly one ownershipDocument in SEC submission")
    document = matches[0].encode("utf-8")
    SecClient._decode(document, "xml")
    return document


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
        return self._fetch(url, refresh=refresh, kind="json")

    def fetch_xml(self, url: str, *, refresh: bool = False) -> Snapshot:
        return self._fetch(url, refresh=refresh, kind="xml")

    def fetch_text(self, url: str, *, refresh: bool = False) -> Snapshot:
        return self._fetch(url, refresh=refresh, kind="text")

    def fetch_document(self, url: str, *, refresh: bool = False) -> Snapshot:
        """Fetch and archive a bounded raw primary document from SEC Archives."""
        return self._fetch(url, refresh=refresh, kind="document")

    @staticmethod
    def _decode(raw, kind):
        if kind == "document":
            if len(raw) > 50_000_000:
                raise ValueError("SEC filing document exceeds the 50 MB limit")
            return raw
        if kind == "text":
            if len(raw) > 50_000_000:
                raise ValueError("SEC complete submission exceeds 50MB limit")
            return raw
        if kind == "json":
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("SEC JSON must be an object")
            return payload
        if len(raw) > 10_000_000:
            raise ValueError("Ownership XML exceeds size limit")
        decoded = raw.decode("utf-8-sig")
        if re.match(r"\s*(?:<!DOCTYPE\s+html\b|<html\b)", decoded, flags=re.IGNORECASE):
            raise UnsupportedSourceFormat(
                "UNSUPPORTED_SOURCE_FORMAT: SEC returned an HTML viewer instead of raw ownership XML"
            )
        decoded = strip_external_ownership_doctype(decoded)
        root = ET.fromstring(decoded)
        if root.tag.rsplit("}", 1)[-1] != "ownershipDocument":
            raise ValueError("Expected raw ownership XML")
        return raw

    def _fetch(self, url: str, *, refresh: bool, kind: str) -> Snapshot:
        parsed = urlsplit(url)
        if kind == "json":
            valid_host = parsed.netloc == "data.sec.gov"
        else:
            valid_host = parsed.netloc == "www.sec.gov"
            if valid_host and kind == "xml":
                valid_host = bool(re.fullmatch(
                    r"/Archives/edgar/data/[0-9]+/[0-9]{18}/(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*"
                    r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.xml", parsed.path, flags=re.IGNORECASE))
            elif valid_host and kind == "text":
                valid_host = bool(re.fullmatch(
                    r"/Archives/edgar/data/[0-9]+/[0-9]{18}/[0-9]{10}-[0-9]{2}-[0-9]{6}\.txt",
                    parsed.path, flags=re.IGNORECASE))
            elif valid_host and kind == "document":
                valid_host = bool(re.fullmatch(
                    r"/Archives/edgar/data/[0-9]+/[0-9]{18}/(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*"
                    r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:htm|html|xml|txt|xhtml)",
                    parsed.path, flags=re.IGNORECASE))
            else:
                valid_host = False
        if parsed.scheme != "https" or not valid_host or parsed.query or parsed.fragment:
            raise ValueError("URL must be a permitted SEC data, raw ownership XML, submission text, or SEC Archives document URL")
        key = hashlib.sha256(url.encode()).hexdigest()
        folder = self.cache_dir / key
        pointer = folder / "latest.json"
        if pointer.exists() and not refresh:
            metadata = json.loads(pointer.read_text())
            digest = metadata["sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or metadata["source_url"] != url:
                raise ValueError("Invalid snapshot metadata")
            path = folder / f"{digest}.{kind}"
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("Cached snapshot checksum mismatch")
            return Snapshot(self._decode(raw, kind), url, metadata["observed_at_utc"], digest, path, True)
        for attempt in range(self.retries + 1):
            self._throttle()
            accept = {"json": "application/json", "xml": "application/xml", "text": "text/plain",
                      "document": "text/html,application/xhtml+xml,application/xml,text/plain"}[kind]
            request = Request(url, headers={"User-Agent": self.user_agent, "Accept": accept})
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
        payload = self._decode(raw, kind)
        digest = hashlib.sha256(raw).hexdigest()
        observed = utc_text(self.now())
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{digest}.{kind}"
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

