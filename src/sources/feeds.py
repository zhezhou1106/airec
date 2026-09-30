"""RSS / Atom feeds. Each feed says which track it feeds:

    knowledge   research blogs and essays, judged like papers
    news        lab announcements and newsletters, summarized in the news section
"""
from __future__ import annotations

from typing import Any

import feedparser

from sources.base import Item, Source, canonical_id, clean, struct_epoch


class Feeds(Source):
    name = "feeds"

    def __init__(self, ctx, conf, track: str = "knowledge"):
        super().__init__(ctx, conf)
        self.track = track

    def feeds(self) -> list[dict[str, Any]]:
        listed = list(self.conf.get("list") or [])
        listed += [dict(f, track=f.get("track", "knowledge")) for f in self.ctx.topic_watch("feeds")
                   if isinstance(f, dict)]
        seen: set[str] = set()
        out = []
        for f in listed:
            if f.get("url") and f["url"] not in seen and f.get("track", "knowledge") == self.track:
                seen.add(f["url"])
                out.append(f)
        return out

    def fetch(self, since: float, until: float) -> list[Item]:
        items: list[Item] = []
        kind = "news" if self.track == "news" else "post"
        feeds = self.feeds()
        for f in feeds:
            name = f.get("name") or f["url"]
            try:
                resp = self.http.get(f["url"])
                resp.raise_for_status()
                parsed = feedparser.parse(resp.text)
            except Exception as exc:  # noqa: BLE001
                self.warn(f"{name}: {exc}")
                continue
            for e in parsed.entries[:50]:
                published = (struct_epoch(getattr(e, "published_parsed", None))
                             or struct_epoch(getattr(e, "updated_parsed", None)) or 0.0)
                if not since <= published <= until:
                    continue
                link = e.get("link", "")
                items.append({
                    "id": canonical_id(link),
                    "kind": kind,
                    "source": f"feed:{name}",
                    "url": link,
                    "title": clean(e.get("title")),
                    "abstract": clean(e.get("summary"))[:4000],
                    "authors": e.get("author", "") or name,
                    "published_at": published,
                    "signals": {"feed": name},
                })
        self.log(f"  feeds ({self.track}): {len(items)} posts from {len(feeds)} feeds")
        return items
