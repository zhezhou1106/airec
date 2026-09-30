"""What every source shares.

A source is a class with a `name`, a `track` ("knowledge" or "news") and one
method, `fetch(since, until) -> list[item]`. An item is a plain dict:

    id            canonical id: arxiv:2509.12345, gh:owner/repo, hf:org/model, url:<hash>
    kind          paper | post | repo | model | news
    source        the source's name (hf_papers, feed:Qwen blog, ...)
    url, title, abstract, authors
    published_at  epoch seconds
    signals       anything the judge may use: stars, upvotes, points, code_url, ...

Sources never raise for a dead feed or a rate limit: they log a warning and
return what they have. Collection degrades, it never fails.
"""
from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

import httpx

from airec.config import USER_AGENT, Config

Item = dict[str, Any]
ARXIV_ID_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", re.I)
GITHUB_RE = re.compile(r"https?://github\.com/[\w.\-]+/[\w.\-]+")
_TRACKING = re.compile(r"^(utm_|ref|ref_src|source|fbclid|gclid|mc_cid|mc_eid)")


@dataclass
class SourceContext:
    cfg: Config
    log: Callable[[str], None] = print
    http: httpx.Client = field(default_factory=lambda: httpx.Client(
        timeout=45, follow_redirects=True, headers={"User-Agent": USER_AGENT}))

    def topic_queries(self, source: str) -> list[str]:
        out: list[str] = []
        for t in self.cfg.topics():
            out.extend((t.get("queries") or {}).get(source) or [])
        return list(dict.fromkeys(q.strip() for q in out if q.strip()))

    def topic_query_groups(self, source: str) -> list[tuple[str, list[str]]]:
        """[(topic id, its queries for this source)], skipping topics without any."""
        out = []
        for t in self.cfg.topics():
            qs = [q.strip() for q in (t.get("queries") or {}).get(source) or [] if q.strip()]
            if qs:
                out.append((t.get("id", ""), list(dict.fromkeys(qs))))
        return out

    def topic_watch(self, key: str) -> list[Any]:
        out: list[Any] = []
        for t in self.cfg.topics():
            out.extend((t.get("watch") or {}).get(key) or [])
        return out


class Source:
    name = "base"
    track = "knowledge"

    def __init__(self, ctx: SourceContext, conf: dict[str, Any]):
        self.ctx = ctx
        self.conf = conf or {}
        self.http = ctx.http

    @property
    def enabled(self) -> bool:
        return bool(self.conf.get("enabled", True))

    def log(self, msg: str) -> None:
        self.ctx.log(msg)

    def warn(self, msg: str) -> None:
        self.ctx.log(f"[warn] {self.name}: {msg}")

    def fetch(self, since: float, until: float) -> list[Item]:
        raise NotImplementedError


# ---------------------------------------------------------------- helpers

def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url)
    except ValueError:
        return url
    query = "&".join(part for part in (p.query or "").split("&")
                     if part and not _TRACKING.match(part.split("=")[0]))
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme, p.netloc.lower().replace("www.", ""), path, "", query, ""))


def canonical_id(url: str, kind: str = "post") -> str:
    m = ARXIV_ID_RE.search(url or "")
    if m:
        return f"arxiv:{m.group(1)}"
    p = urlparse(url or "")
    if kind == "repo" and p.netloc.endswith("github.com"):
        parts = [x for x in p.path.split("/") if x]
        if len(parts) >= 2:
            return f"gh:{parts[0].lower()}/{parts[1].lower()}"
    return "url:" + hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def struct_epoch(value: Any) -> float | None:
    try:
        return float(calendar.timegm(value))
    except Exception:  # noqa: BLE001
        return None


def iso_epoch(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def merge(items: list[Item]) -> list[Item]:
    """One entry per id; signals and 'seen_in' merged, the longest abstract kept."""
    merged: dict[str, Item] = {}
    for it in items:
        key = it["id"]
        if key not in merged:
            it.setdefault("signals", {})["seen_in"] = [it["source"]]
            merged[key] = it
            continue
        target = merged[key]
        target["signals"].update({k: v for k, v in (it.get("signals") or {}).items()
                                  if v not in ("", None)})
        seen = target["signals"].setdefault("seen_in", [])
        if it["source"] not in seen:
            seen.append(it["source"])
        if len(it.get("abstract") or "") > len(target.get("abstract") or ""):
            target["abstract"] = it["abstract"]
        if not target.get("authors") and it.get("authors"):
            target["authors"] = it["authors"]
    return list(merged.values())
