"""Small text, URL and date helpers shared by the sources."""

from __future__ import annotations

import calendar
import re
import time
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

GITHUB_URL = re.compile(r"https?://github\.com/[\w.\-]+/[\w.\-]+")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_TRACKING = re.compile(r"^(utm_|ref$|ref_src$|source$|fbclid$|gclid$|mc_cid$|mc_eid$)")


def clean_text(text: str | None) -> str:
    """Strip tags and collapse whitespace."""
    return _SPACE.sub(" ", _TAG.sub(" ", text or "")).strip()


def canonical_url(url: str) -> str:
    """A URL without tracking parameters, for spotting the same link twice."""
    parts = urlparse(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)])
    host = parts.netloc.lower().removeprefix("www.")
    return urlunparse((parts.scheme, host, parts.path.rstrip("/"), "", query, ""))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def from_struct(value: time.struct_time | None) -> datetime | None:
    """Convert feedparser's UTC struct_time."""
    return datetime.fromtimestamp(calendar.timegm(value), UTC) if value else None
