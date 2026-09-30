"""Where people talk about new things: Hacker News and Reddit (news track)."""
from __future__ import annotations

import time

import feedparser

from sources.base import Item, Source, canonical_id, clean, struct_epoch

HN_API = "https://hn.algolia.com/api/v1/search_by_date"


class HackerNews(Source):
    name = "hackernews"
    track = "news"

    def fetch(self, since: float, until: float) -> list[Item]:
        min_points = int(self.conf.get("min_points", 80))
        queries = list(dict.fromkeys(list(self.conf.get("queries") or [])
                                     + self.ctx.topic_queries("hackernews")))
        items: dict[str, Item] = {}
        for query in queries:
            try:
                resp = self.http.get(HN_API, params={
                    "query": query, "tags": "story", "hitsPerPage": 40,
                    "numericFilters": f"created_at_i>{int(since)},created_at_i<{int(until)},"
                                      f"points>{min_points}"})
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                self.warn(f"'{query}': {exc}")
                continue
            for hit in resp.json().get("hits", []):
                hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                link = hit.get("url") or hn_url
                it = {
                    "id": canonical_id(link),
                    "kind": "news",
                    "source": self.name,
                    "url": link,
                    "title": hit.get("title", ""),
                    "abstract": clean(hit.get("story_text"))[:2000],
                    "authors": hit.get("author", ""),
                    "published_at": float(hit.get("created_at_i") or 0),
                    "signals": {"hn_points": hit.get("points"),
                                "hn_comments": hit.get("num_comments"), "hn_url": hn_url},
                }
                items.setdefault(it["id"], it)
        self.log(f"  hackernews: {len(items)} stories")
        return list(items.values())


class Reddit(Source):
    name = "reddit"
    track = "news"

    def fetch(self, since: float, until: float) -> list[Item]:
        items: list[Item] = []
        subs = self.conf.get("subreddits") or []
        for n, sub in enumerate(subs):
            if n:
                time.sleep(3)  # Reddit rate-limits anonymous RSS quickly
            parsed = None
            for attempt in range(2):
                try:
                    resp = self.http.get(f"https://www.reddit.com/r/{sub}/top/.rss?t=week")
                    if resp.status_code == 429 and attempt == 0:
                        time.sleep(10)
                        continue
                    resp.raise_for_status()
                    parsed = feedparser.parse(resp.text)
                    break
                except Exception as exc:  # noqa: BLE001
                    self.warn(f"r/{sub}: {exc}")
                    break
            if parsed is None:
                continue
            for e in parsed.entries[: int(self.conf.get("per_sub", 25))]:
                published = (struct_epoch(getattr(e, "published_parsed", None))
                             or struct_epoch(getattr(e, "updated_parsed", None)) or 0.0)
                if not since <= published <= until:
                    continue
                link = e.get("link", "")
                items.append({
                    "id": canonical_id(link),
                    "kind": "news",
                    "source": f"reddit:{sub}",
                    "url": link,
                    "title": clean(e.get("title")),
                    "abstract": clean(e.get("summary"))[:2000],
                    "authors": e.get("author", ""),
                    "published_at": published,
                    "signals": {"subreddit": sub},
                })
        self.log(f"  reddit: {len(items)} posts from {len(subs)} subreddits")
        return items
