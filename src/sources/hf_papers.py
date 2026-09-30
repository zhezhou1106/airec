"""Hugging Face daily papers: what the community upvoted, day by day."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sources.base import GITHUB_RE, Item, Source, clean, iso_epoch

API = "https://huggingface.co/api/daily_papers"


class HfPapers(Source):
    name = "hf_papers"
    track = "knowledge"

    def fetch(self, since: float, until: float) -> list[Item]:
        min_up = int(self.conf.get("min_upvotes", 1))
        items: list[Item] = []
        day = datetime.fromtimestamp(since, tz=timezone.utc).date()
        last = min(datetime.fromtimestamp(until, tz=timezone.utc).date(),
                   datetime.now(timezone.utc).date())
        while day <= last:
            date = day.isoformat()
            day += timedelta(days=1)
            try:
                resp = self.http.get(API, params={"date": date})
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                self.warn(f"{date}: {exc}")
                continue
            for entry in payload if isinstance(payload, list) else []:
                paper = entry.get("paper") or {}
                pid = paper.get("id")
                upvotes = int(paper.get("upvotes") or 0)
                if not pid or upvotes < min_up:
                    continue
                summary = clean(paper.get("summary"))
                code = paper.get("githubRepo") or ""
                if not code:
                    m = GITHUB_RE.search(summary)
                    code = m.group(0) if m else ""
                items.append({
                    "id": f"arxiv:{pid}",
                    "kind": "paper",
                    "source": self.name,
                    "url": f"https://arxiv.org/abs/{pid}",
                    "title": clean(paper.get("title")),
                    "abstract": summary,
                    "authors": ", ".join(a.get("name", "") for a in
                                         (paper.get("authors") or [])[:12]),
                    "published_at": iso_epoch(paper.get("publishedAt")) or since,
                    "signals": {"hf_upvotes": upvotes, "code_url": code},
                })
        self.log(f"  hf_papers: {len(items)} papers")
        return items
