"""Semantic Scholar: per-topic searches across arXiv and conference/journal papers.

One bulk-search request per topic: the topic's `queries.arxiv` phrases OR-ed
together, limited to the window. Papers with an arXiv id get the same id as the
arXiv source, so the two merge into one item. Citation counts become signals.

Works without a key on the shared public pool (slower, sometimes throttled);
SEMANTIC_SCHOLAR_API_KEY in Settings → Keys gives a steady 1 request per second.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from airec.sources.base import GITHUB_RE, Item, Source, clean, iso_epoch

API = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
KEY = "SEMANTIC_SCHOLAR_API_KEY"
FIELDS = ("title,externalIds,abstract,publicationDate,citationCount,influentialCitationCount,"
          "venue,authors,url")


def _day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


class SemanticScholar(Source):
    name = "semantic_scholar"
    track = "knowledge"

    def fetch(self, since: float, until: float) -> list[Item]:
        key = self.ctx.cfg.secret(KEY)
        headers = {"x-api-key": key} if key else {}
        pause = 1.1 if key else 2.0
        per_topic = int(self.conf.get("per_topic", 200))
        groups = self.ctx.topic_query_groups("arxiv")
        items: dict[str, Item] = {}
        failures = 0
        for n, (topic, phrases) in enumerate(groups):
            query = " | ".join(f'"{p.replace(chr(34), "")}"' for p in phrases)
            got = self._search(query, since, until, per_topic, headers)
            if got is None:
                failures += 1
                if failures >= 2:
                    self.warn(f"keeps refusing (rate limit); skipping the other "
                              f"{len(groups) - n - 1} topics this run"
                              + ("" if key else ". An API key makes this steadier."))
                    break
                continue
            failures = 0
            for it in got:
                items.setdefault(it["id"], it)
            time.sleep(pause)
        self.log(f"  semantic_scholar: {len(items)} papers from {len(groups)} topic searches"
                 + ("" if key else " (no API key: shared pool)"))
        return list(items.values())

    def _search(self, query: str, since: float, until: float, limit: int,
                headers: dict[str, str]) -> list[Item] | None:
        params: dict[str, Any] = {"query": query, "fields": FIELDS,
                                  "publicationDateOrYear": f"{_day(since)}:{_day(until)}",
                                  "sort": "publicationDate:desc"}
        out: list[Item] = []
        while len(out) < limit:
            data = self._get(params, headers)
            if data is None:
                return out or None
            for p in data.get("data") or []:
                it = _item(p)
                if it and since - 86400 <= it["published_at"] <= until + 86400:
                    out.append(it)
            token = data.get("token")
            if not token or not data.get("data"):
                break
            params["token"] = token
        return out[:limit]

    def _get(self, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any] | None:
        for attempt in range(3):
            try:
                resp = self.http.get(API, params=params, headers=headers, timeout=60)
            except Exception as exc:  # noqa: BLE001
                self.warn(str(exc))
                time.sleep(5 * (attempt + 1))
                continue
            if resp.status_code == 429:
                wait = resp.headers.get("Retry-After", "")
                time.sleep(min(int(wait), 60) if wait.isdigit() else 10 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                self.warn(f"HTTP {resp.status_code}: {resp.text[:160]}")
                return None
            return resp.json()
        self.warn("rate limited (HTTP 429)")
        return None


def _item(p: dict[str, Any]) -> Item | None:
    title = clean(p.get("title"))
    if not title:
        return None
    arxiv_id = (p.get("externalIds") or {}).get("ArXiv")
    abstract = clean(p.get("abstract"))
    code = GITHUB_RE.search(abstract)
    signals: dict[str, Any] = {
        "citations": p.get("citationCount") or 0,
        "influential_citations": p.get("influentialCitationCount") or 0,
        "venue": p.get("venue") or "",
        "code_url": code.group(0) if code else "",
    }
    return {
        "id": f"arxiv:{arxiv_id}" if arxiv_id else f"s2:{p.get('paperId', '')}",
        "kind": "paper",
        "source": "semantic_scholar",
        "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (p.get("url") or ""),
        "title": title,
        "abstract": abstract,
        "authors": ", ".join(a.get("name", "") for a in (p.get("authors") or [])[:12]),
        "published_at": iso_epoch(p.get("publicationDate")) or 0.0,
        "signals": signals,
    }
