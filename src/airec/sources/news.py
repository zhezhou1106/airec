"""News sources: feeds, Hacker News, Reddit, Hugging Face models and web search.

Each returns `Story` records for one window; ranking and merging happen in the
ai_news section.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import feedparser

from airec.http import Http
from airec.sources.common import canonical_url, clean_text, from_struct, parse_iso

HN_API = "https://hn.algolia.com/api/v1/search_by_date"
HF_MODELS = "https://huggingface.co/api/models"
TAVILY = "https://api.tavily.com/search"
# Format re-uploads are not new models.
_REPACK = re.compile(r"(gguf|gptq|awq|exl2|mlx|[-_](4|8)bit|[-_]fp8|[-_]nvfp4|lora)\b", re.I)


@dataclass
class Story:
    title: str
    url: str
    summary: str
    source: str
    published_at: datetime
    popularity: int = 0  # the source's own signal: points, likes, ...
    primary: bool = False  # from the people who made the thing, not coverage of it

    @property
    def id(self) -> str:
        return hashlib.sha1(canonical_url(self.url).encode()).hexdigest()[:16]


def _feed_stories(
    content: bytes,
    source: str,
    since: datetime,
    until: datetime,
    *,
    limit: int,
    summary_chars: int,
    primary: bool = False,
) -> list[Story]:
    """The first `limit` entries of an RSS or Atom feed that are dated inside the window."""
    stories = []
    for entry in feedparser.parse(content).entries[:limit]:
        published = from_struct(entry.get("published_parsed") or entry.get("updated_parsed"))
        link = entry.get("link", "")
        if published is None or not link or not since <= published <= until:
            continue
        stories.append(
            Story(
                title=clean_text(entry.get("title")),
                url=link,
                summary=clean_text(entry.get("summary"))[:summary_chars],
                source=source,
                published_at=published,
                primary=primary,
            )
        )
    return stories


def read_feed(
    http: Http, name: str, url: str, since: datetime, until: datetime, *, primary: bool
) -> list[Story]:
    content = http.get(url, attempts=2).content
    return _feed_stories(content, name, since, until, limit=60, summary_chars=1000, primary=primary)


def hacker_news(
    http: Http, queries: list[str], min_points: int, since: datetime, until: datetime
) -> list[Story]:
    stories = []
    for query in queries:
        filters = (
            f"created_at_i>{int(since.timestamp())},created_at_i<{int(until.timestamp())},"
            f"points>={min_points}"
        )
        response = http.get(
            HN_API,
            params={"query": query, "tags": "story", "hitsPerPage": 30, "numericFilters": filters},
        )
        for hit in response.json().get("hits", []):
            discussion = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            stories.append(
                Story(
                    title=hit.get("title") or "",
                    url=hit.get("url") or discussion,
                    summary=clean_text(hit.get("story_text"))[:600],
                    source="Hacker News",
                    published_at=parse_iso(hit.get("created_at")) or until,
                    popularity=int(hit.get("points") or 0),
                )
            )
    return stories


def reddit_top(
    http: Http, subreddit: str, limit: int, since: datetime, until: datetime
) -> list[Story]:
    # Reddit's top list only offers fixed spans; a catch-up window needs the wider one.
    span = "month" if until - since > timedelta(days=7) else "week"
    response = http.get(f"https://www.reddit.com/r/{subreddit}/top/.rss", params={"t": span})
    source = f"r/{subreddit}"
    return _feed_stories(response.content, source, since, until, limit=limit, summary_chars=600)


def _size_label(parameters: float) -> str:
    return f"{parameters / 1e9:.1f}B" if parameters >= 1e9 else f"{parameters / 1e6:.0f}M"


def new_hf_models(http: Http, min_likes: int, since: datetime, until: datetime) -> list[Story]:
    """New models from their original authors that are trending on Hugging Face."""
    expand = ["safetensors", "createdAt", "likes", "pipeline_tag", "cardData", "author"]
    params = [("sort", "trendingScore"), ("direction", "-1"), ("limit", "200")]
    params += [("expand[]", value) for value in expand]
    stories = []
    for model in http.get(HF_MODELS, params=params).json():
        model_id = model.get("id", "")
        created = parse_iso(model.get("createdAt"))
        likes = int(model.get("likes") or 0)
        if created is None or not since <= created <= until or likes < min_likes:
            continue
        card = model.get("cardData") or {}
        base = card.get("base_model")
        base = base[0] if isinstance(base, list) and base else base
        owner = model_id.split("/")[0].lower()
        derived = isinstance(base, str) and "/" in base and base.split("/")[0].lower() != owner
        if _REPACK.search(model_id) or derived:
            continue
        facts = [f"{model.get('pipeline_tag') or 'model'} by {owner}"]
        total = (model.get("safetensors") or {}).get("total")
        if total:
            facts.append(f"{_size_label(total)} parameters")
        if card.get("license"):
            facts.append(f"license {card['license']}")
        stories.append(
            Story(
                title=f"New on Hugging Face: {model_id}",
                url=f"https://huggingface.co/{model_id}",
                summary=", ".join(facts),
                source="Hugging Face",
                published_at=created,
                popularity=likes,
                primary=True,
            )
        )
    return stories


def web_search(
    http: Http, api_key: str, query: str, days: int, limit: int, undated: datetime
) -> list[Story]:
    """Recent news from Tavily, which reaches announcements that have no feed."""
    response = http.post(
        TAVILY,
        json={"query": query, "topic": "news", "days": days, "max_results": limit},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    stories = []
    for result in response.json().get("results", []):
        stories.append(
            Story(
                title=result.get("title") or "",
                url=result.get("url") or "",
                summary=(result.get("content") or "")[:800],
                source="web",
                published_at=_web_date(result.get("published_date")) or undated,
            )
        )
    return stories


def _web_date(value: str | None) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value or "")
    except (TypeError, ValueError):
        return parse_iso(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
