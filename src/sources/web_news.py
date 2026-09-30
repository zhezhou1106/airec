"""Web news through web search (Tavily, then Brave, then DuckDuckGo): catches
announcements no feed carries.

Runs the general queries in sources.yaml plus each topic's `queries.web`,
restricted to the news window.
"""
from __future__ import annotations

import math
import time

from search import SearchError, WebSearch
from sources.base import Item, Source, canonical_id, iso_epoch


# Papers belong to the knowledge track; these hosts are mostly reposts and commentary.
PAPER_HOSTS = {"arxiv.org", "alphaxiv.org", "semanticscholar.org", "openreview.net",
               "paperswithcode.com", "huggingface.co/papers"}
SKIP_DOMAINS = ["medium.com", "facebook.com", "linkedin.com", "x.com", "twitter.com",
                "youtube.com", "reddit.com", "quora.com", "substack.com/@", "briefflash.com"]


def _skip(url: str, skip_hosts: set[str]) -> bool:
    u = url.lower().split("://", 1)[-1]
    host = u.split("/", 1)[0].removeprefix("www.")
    if any(host == h or host.endswith("." + h) or u.startswith(h) for h in PAPER_HOSTS):
        return True
    return any(host == h or host.endswith("." + h) or u.startswith(h) for h in skip_hosts)


class WebNews(Source):
    name = "web_news"
    track = "news"

    def fetch(self, since: float, until: float) -> list[Item]:
        search = WebSearch(self.ctx.cfg, log=self.log)
        if not search.available:
            self.warn("skipped: no search backend (see Settings → Keys)")
            return []
        days = max(1, math.ceil((until - since) / 86400))
        queries = list(dict.fromkeys(list(self.conf.get("queries") or [])
                                     + self.ctx.topic_queries("web")))
        queries = queries[: int(self.conf.get("max_queries", 20))]
        skip_hosts = set(self.conf.get("skip_domains") or SKIP_DOMAINS)
        items: dict[str, Item] = {}
        for query in queries:
            try:
                results = search.search(query, max_results=int(self.conf.get("per_query", 6)),
                                        news=True, days=days)
            except SearchError as exc:
                self.warn(str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                self.warn(f"'{query}': {exc}")
                continue
            for r in results:
                if not r["url"] or _skip(r["url"], skip_hosts):
                    continue
                published = iso_epoch(r.get("published")) or until
                if published < since:
                    continue
                it = {
                    "id": canonical_id(r["url"]),
                    "kind": "news",
                    "source": self.name,
                    "url": r["url"],
                    "title": r["title"],
                    "abstract": r["content"],
                    "authors": "",
                    "published_at": published,
                    "signals": {"query": query, "engine": r.get("engine", "")},
                }
                items.setdefault(it["id"], it)
            # DuckDuckGo has no API and blocks bursts; the paid APIs do not need the pause.
            time.sleep(2.0 if search.engines[:1] == ["duckduckgo"] else 0.2)
        self.log(f"  web_news: {len(items)} articles from {len(queries)} searches")
        return list(items.values())
