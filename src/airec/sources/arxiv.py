"""arXiv: a full sweep of chosen categories, and optional per-topic searches.

The sweep uses OAI-PMH, arXiv's bulk interface (sweep: oai, the default), and
falls back to the search API; `sweep: api` does it the other way round. The
search API allows about one request every 3 seconds, with no way to raise it,
so per-topic searches are done by the semantic_scholar source by default;
`topic_search: true` does them here instead.
`harvest()` is the bulk path used by the index backfill.
"""
from __future__ import annotations

import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterator

import feedparser

from airec.config import USER_AGENT
from airec.sources.base import GITHUB_RE, Item, Source, clean, struct_epoch

API = "https://export.arxiv.org/api/query"
OAI = "https://oaipmh.arxiv.org/oai"
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "ax": "http://arxiv.org/OAI/arXivRaw/"}


def _tls() -> ssl.SSLContext:
    # export.arxiv.org's CDN has answered 406 to default OpenSSL handshakes;
    # a narrower TLS 1.2 cipher list avoids that.
    ctx = ssl.create_default_context()
    ctx.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20")
    return ctx


_TLS = _tls()


def api_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/xml;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout, context=_TLS) as resp:
        return resp.read()


def make_item(arxiv_id: str, title: str, abstract: str, authors: list[str],
              categories: list[str], published: float, source: str = "arxiv") -> Item:
    abstract = clean(abstract)
    code = GITHUB_RE.search(abstract)
    return {
        "id": f"arxiv:{arxiv_id}",
        "kind": "paper",
        "source": source,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "title": clean(title),
        "abstract": abstract,
        "authors": ", ".join(a for a in authors[:12] if a),
        "published_at": published,
        "signals": {"categories": categories[:6], "code_url": code.group(0) if code else ""},
    }


def _entry_id(link: str) -> str:
    m = re.search(r"(\d{4}\.\d{4,5})", link or "")
    return m.group(1) if m else ""


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y%m%d%H%M")


class Arxiv(Source):
    name = "arxiv"
    track = "knowledge"

    def fetch(self, since: float, until: float) -> list[Item]:
        cats = self.conf.get("categories") or ["cs.LG", "cs.CL", "cs.AI"]
        max_items = int(self.conf.get("max_items", 5000))
        items: list[Item] = []
        if self.conf.get("sweep", "oai") == "oai":
            # The bulk interface: ~1,000 records per request, no search-API rate limit.
            try:
                items = self._oai(cats, since, until)[:max_items]
            except Exception as exc:  # noqa: BLE001
                self.warn(f"OAI-PMH failed ({exc}); using the search API")
                items, _ok = self._sweep(cats, max_items, since, until)
        else:
            items, ok = self._sweep(cats, max_items, since, until)
            if not ok:
                self.log("  arxiv: search API failing, using OAI-PMH")
                have = {it["id"] for it in items}
                for it in self._oai(cats, since, until):
                    if it["id"] not in have:
                        items.append(it)
        swept = len(items)
        if self.conf.get("topic_search", False):
            have = {it["id"] for it in items}
            per_query = int(self.conf.get("per_query", 40))
            groups = self.ctx.topic_query_groups("arxiv")
            failures = 0
            for n, (topic, phrases) in enumerate(groups):
                # One request per topic (its phrases OR-ed together), not one per phrase.
                found = self._search(phrases, per_query * min(len(phrases), 3), since, until)
                if found is None:
                    failures += 1
                    if failures >= 2:
                        self.warn(f"arXiv keeps refusing (rate limit); skipping the other "
                                  f"{len(groups) - n - 1} topic searches this run. The "
                                  "category sweep still covers your categories.")
                        break
                    continue
                failures = 0
                for it in found:
                    if it["id"] not in have:
                        have.add(it["id"])
                        items.append(it)
        self.log(f"  arxiv: {swept} from the category sweep, "
                 f"{len(items) - swept} more from topic searches")
        return items

    # ---------- search API ----------
    def _get_feed(self, url: str, attempts: int = 3) -> Any | None:
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return feedparser.parse(api_get(url))
            except Exception as exc:  # noqa: BLE001
                last = exc
                code = getattr(exc, "code", None)
                if isinstance(exc, urllib.error.HTTPError) and code != 429 and 400 <= code < 500:
                    break
                if attempt == attempts - 1:
                    break
                if code == 429:
                    # arXiv is asking us to slow down: wait what it says, or a good while.
                    wait = (getattr(exc, "headers", None) or {}).get("Retry-After", "")
                    time.sleep(min(int(wait), 120) if str(wait).isdigit() else 20 * (attempt + 1))
                else:
                    time.sleep(3 * (attempt + 1))
        self.warn(f"search API: {last}")
        return None

    def _entries(self, feed: Any, source: str) -> Iterator[Item]:
        for e in feed.entries:
            aid = _entry_id(e.get("id") or e.get("link", ""))
            if not aid:
                continue
            yield make_item(aid, e.get("title", ""), e.get("summary", ""),
                            [a.get("name", "") for a in getattr(e, "authors", [])],
                            [t.get("term") for t in getattr(e, "tags", [])],
                            struct_epoch(getattr(e, "published_parsed", None)) or 0.0, source)

    def _sweep(self, cats: list[str], max_items: int, since: float, until: float
               ) -> tuple[list[Item], bool]:
        query = "+OR+".join(f"cat:{c}" for c in cats)
        items: list[Item] = []
        start, page = 0, 200
        while start < max_items:
            feed = self._get_feed(f"{API}?search_query={query}&start={start}&max_results={page}"
                                  "&sortBy=submittedDate&sortOrder=descending")
            if feed is None:
                return items, False
            if not feed.entries:
                break
            older = False
            for it in self._entries(feed, "arxiv"):
                if it["published_at"] < since:
                    older = True
                elif it["published_at"] <= until:
                    items.append(it)
            if older:
                break
            start += page
            time.sleep(3)  # arXiv asks for one request every 3 seconds
        return items, True

    def _search(self, phrases: list[str], limit: int, since: float, until: float
                ) -> list[Item] | None:
        """Papers in the window matching any phrase; None when arXiv did not answer."""
        terms = " OR ".join(f'ti:"{p}" OR abs:"{p}"'
                            for p in (x.replace('"', "") for x in phrases))
        q = f"({terms}) AND submittedDate:[{_stamp(since)} TO {_stamp(until)}]"
        url = (f"{API}?search_query={urllib.parse.quote(q)}&max_results={limit}"
               "&sortBy=submittedDate&sortOrder=descending")
        time.sleep(3)
        feed = self._get_feed(url, attempts=2)
        if feed is None:
            return None
        return [it for it in self._entries(feed, "arxiv") if since <= it["published_at"] <= until]

    # ---------- OAI-PMH ----------
    def _oai(self, cats: list[str], since: float, until: float) -> list[Item]:
        start = datetime.fromtimestamp(since, tz=timezone.utc).date()
        end = datetime.fromtimestamp(until, tz=timezone.utc).date()
        return [it for it in harvest(self.http, cats, start, end, self.warn)
                if since <= it["published_at"] <= until]


def harvest(http: Any, cats: list[str], start: Any, end: Any,
            warn: Callable[[str], None] = print, first_version_after: float = 0.0
            ) -> Iterator[Item]:
    """Every paper in `cats` whose record changed between two dates (OAI-PMH).

    OAI datestamps move on every revision, so records are filtered on the v1
    date: only papers first submitted after `first_version_after` are yielded.
    """
    wanted = set(cats)
    for oai_set in sorted({c.split(".")[0] for c in cats}):
        params: dict[str, str] = {"verb": "ListRecords", "metadataPrefix": "arXivRaw",
                                  "set": oai_set, "from": start.isoformat(),
                                  "until": end.isoformat()}
        while True:
            root = None
            for attempt in range(4):
                try:
                    resp = http.get(OAI, params=params, timeout=180)
                    if resp.status_code == 503:
                        wait = resp.headers.get("Retry-After", "")
                        time.sleep(min(int(wait), 120) if wait.isdigit() else 20)
                        continue
                    resp.raise_for_status()
                    root = ET.fromstring(resp.content)
                    break
                except Exception as exc:  # noqa: BLE001
                    warn(f"OAI-PMH: {exc}")
                    time.sleep(10 * (attempt + 1))
            if root is None:
                raise RuntimeError("arXiv OAI-PMH did not answer after 4 attempts")
            err = root.find("oai:error", NS)
            if err is not None:
                if err.get("code") != "noRecordsMatch":
                    warn(f"OAI-PMH {err.get('code')}: {err.text}")
                break
            for rec in root.iterfind(".//oai:record", NS):
                meta = rec.find("oai:metadata/ax:arXivRaw", NS)
                if meta is None:
                    continue
                categories = (meta.findtext("ax:categories", "", NS) or "").split()
                if not wanted.intersection(categories):
                    continue
                v1 = meta.findtext("ax:version[@version='v1']/ax:date", "", NS)
                try:
                    published = parsedate_to_datetime(v1).timestamp()
                except (TypeError, ValueError):
                    continue
                if published < first_version_after:
                    continue
                authors = [a.strip() for a in re.split(r",|\band\b",
                           meta.findtext("ax:authors", "", NS) or "") if a.strip()]
                yield make_item(meta.findtext("ax:id", "", NS).strip(),
                                meta.findtext("ax:title", "", NS),
                                meta.findtext("ax:abstract", "", NS),
                                authors, categories, published)
            token = root.find(".//oai:resumptionToken", NS)
            if token is None or not (token.text or "").strip():
                break
            params = {"verb": "ListRecords", "resumptionToken": token.text.strip()}
            time.sleep(1)


def month_ranges(months: int, today: Any = None) -> list[tuple[Any, Any]]:
    """[(first_day, last_day)] for the last `months` months, oldest first."""
    today = today or datetime.now(timezone.utc).date()
    out = []
    end = today
    for _ in range(months):
        start = (end.replace(day=1) if end.day > 1
                 else (end - timedelta(days=1)).replace(day=1))
        out.append((start, end))
        end = start - timedelta(days=1)
    return list(reversed(out))
