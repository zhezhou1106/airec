"""What happened in AI this week: releases, techniques, companies, tools.

Sources are cast widely and ranked cheaply; one editor call then merges coverage
of the same event, chooses what matters and sorts it into categories. Every
story must cite candidates by id, so a story without a real source cannot reach
the report.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from airec import prompts
from airec.config import Strict
from airec.sections import register
from airec.sections.base import Section, SectionError, SectionResult, Window
from airec.sources import news
from airec.sources.common import canonical_url
from airec.sources.news import Story

_SUMMARY_CHARS = 450
_REMEMBERED_HEADLINES = 40
_AGGREGATORS = {"Hacker News", "web"}


class Feed(Strict):
    name: str
    url: str
    primary: bool = False  # the lab itself, rather than commentary


class Brief(BaseModel):
    category: str
    headline: str
    summary: str
    sources: list[str]
    importance: Literal["high", "normal"] = "normal"


class Briefing(BaseModel):
    stories: list[Brief]


@register
class AiNews(Section):
    key = "ai_news"
    title = "AI news"

    class Config(Strict):
        categories: list[str] = [
            "Model releases",
            "Techniques & research",
            "Companies & industry",
            "Tools & infrastructure",
        ]
        max_stories: int = Field(default=14, ge=1)
        max_candidates: int = Field(default=120, ge=1)
        feeds: list[Feed] = []
        web_queries: list[str] = []
        web_results: int = Field(default=6, ge=1)
        watch_repos: list[str] = []  # their releases count as news
        hn_queries: list[str] = []
        hn_min_points: int = Field(default=80, ge=0)
        subreddits: list[str] = []
        reddit_limit: int = Field(default=25, ge=1)
        hf_min_likes: int = Field(default=30, ge=0)

    def run(self, window: Window) -> SectionResult:
        stories = self._collect(window)
        self.require_sources()
        candidates = self._rank(self._unseen(stories))
        stats = {"collected": len(stories), "candidates": len(candidates)}
        if not candidates:
            return SectionResult("A quiet week: nothing new from the news sources.", stats=stats)

        labels = {f"s{number}": story for number, story in enumerate(candidates, 1)}
        briefing = self._edit(labels, window)
        briefs = [
            brief for brief in briefing.stories if any(source in labels for source in brief.sources)
        ][: self.config.max_stories]
        if not briefs:
            if briefing.stories:
                raise SectionError("the editor cited no real candidate")
            return SectionResult("A quiet week: nothing notable made the cut.", stats=stats)

        cited = [labels[s] for brief in briefs for s in brief.sources if s in labels]
        important = [brief for brief in briefs if brief.importance == "high"] or briefs
        return SectionResult(
            markdown=self._render(briefs, labels),
            highlights=[f"{brief.headline}" for brief in important[:3]],
            shown=[story.id for story in cited],
            stats={**stats, "stories": len(briefs)},
            commit=lambda: self._remember(briefs),
        )

    def checks(self) -> list[tuple[str, Callable[[], str]]]:
        return [(f"feed: {feed.name}", lambda f=feed: self._probe(f)) for feed in self.config.feeds]

    def _probe(self, feed: Feed) -> str:
        self.ctx.http.get(feed.url, attempts=1, timeout=15)
        return "reachable"

    # ---- collecting ----

    def _collect(self, window: Window) -> list[Story]:
        """Every source at once (a few at a time), in a fixed order so ranking ties are stable."""
        c = self.config
        since, until = window.since, window.until
        http = self.ctx.http
        jobs: list[tuple[str, Callable[[], list[Story]]]] = []
        for feed in c.feeds:
            jobs.append(
                (
                    f"feed {feed.name}",
                    lambda f=feed: news.read_feed(
                        http, f.name, f.url, since, until, primary=f.primary
                    ),
                )
            )
        for query in c.hn_queries:
            jobs.append(
                (
                    f"Hacker News '{query}'",
                    lambda q=query: news.hacker_news(http, [q], c.hn_min_points, since, until),
                )
            )
        for subreddit in c.subreddits:
            jobs.append(
                (
                    f"r/{subreddit}",
                    lambda s=subreddit: news.reddit_top(http, s, c.reddit_limit, since, until),
                )
            )
        jobs.append(
            ("Hugging Face models", lambda: news.new_hf_models(http, c.hf_min_likes, since, until))
        )
        jobs += self._web_jobs(window)
        jobs += self._release_jobs(window)
        return [story for found in self.gather_all(jobs, []) for story in found]

    def _web_jobs(self, window: Window) -> list[tuple[str, Callable[[], list[Story]]]]:
        api_key = self.ctx.secrets.get("TAVILY_API_KEY")
        if not self.config.web_queries:
            return []
        if not api_key:
            self.warn("web search skipped: TAVILY_API_KEY is not set")
            return []
        days = max(1, math.ceil(window.days))

        def search(query: str) -> list[Story]:
            found = news.web_search(
                self.ctx.http, api_key, query, days, self.config.web_results, window.until
            )
            return [story for story in found if story.url and window.contains(story.published_at)]

        return [(f"web search '{q}'", lambda q=q: search(q)) for q in self.config.web_queries]

    def _release_jobs(self, window: Window) -> list[tuple[str, Callable[[], list[Story]]]]:
        github = self.ctx.github

        def releases(repo: str) -> list[Story]:
            return [
                Story(
                    title=f"{release.repo} {release.name}".strip(),
                    url=release.url,
                    summary=release.body[:800],
                    source=release.repo,
                    published_at=release.published_at,
                    primary=True,
                )
                for release in github.releases(repo)
                if window.contains(release.published_at)
            ]

        return [(f"releases of {r}", lambda r=r: releases(r)) for r in self.config.watch_repos]

    # ---- ranking ----

    def _unseen(self, stories: list[Story]) -> list[Story]:
        unique: dict[str, Story] = {}
        for story in stories:
            key = canonical_url(story.url)
            if story.title and key not in unique:
                unique[key] = story
        shown = self.ctx.store.shown_before(
            self.key, [story.id for story in unique.values()], self.ctx.week
        )
        return [story for story in unique.values() if story.id not in shown]

    def _rank(self, stories: list[Story]) -> list[Story]:
        """Cap the editor's input: primary sources first, then by popularity, newest first."""
        ordered = sorted(
            stories,
            key=lambda s: (s.primary, s.popularity, s.published_at),
            reverse=True,
        )
        return ordered[: self.config.max_candidates]

    # ---- editing ----

    def _edit(self, labels: dict[str, Story], window: Window) -> Briefing:
        blocks = []
        for label, story in labels.items():
            detail = f" [{story.popularity}]" if story.popularity else ""
            summary = " ".join(story.summary.split())[:_SUMMARY_CHARS]
            blocks.append(
                f"{label} ({story.source}{detail}, {story.published_at:%b %-d}): "
                f"{story.title}\n    {summary}"
            )
        earlier = [
            entry["headline"]
            for entry in self.ctx.store.get_state(self.key, "recent_headlines", [])
            if entry["week"] < self.ctx.week
        ]
        system = prompts.render(
            "ai_news",
            reader=self.ctx.reader,
            window=window.label,
            categories="\n".join(f"- {category}" for category in self.config.categories),
            max_stories=self.config.max_stories,
            earlier="\n".join(f"- {headline}" for headline in earlier) or "(none)",
        )
        return self.ctx.llm.json("writer", system, "\n".join(blocks), Briefing)

    def _remember(self, briefs: list[Brief]) -> None:
        """Keep headlines so a follow-up article is not reported as new next week."""
        week = self.ctx.week
        kept = self.ctx.store.get_state(self.key, "recent_headlines", [])
        kept = [entry for entry in kept if entry["week"] < week]
        fresh = [{"week": week, "headline": brief.headline} for brief in briefs]
        merged = (fresh + kept)[:_REMEMBERED_HEADLINES]
        self.ctx.store.set_state(self.key, "recent_headlines", merged)

    # ---- rendering ----

    def _render(self, briefs: list[Brief], labels: dict[str, Story]) -> str:
        order = list(self.config.categories)
        order += [b.category for b in briefs if b.category not in order]
        blocks = []
        for category in dict.fromkeys(order):
            members = [brief for brief in briefs if brief.category == category]
            if not members:
                continue
            members.sort(key=lambda brief: brief.importance != "high")
            blocks.append(f"### {category}\n")
            blocks += [_bullet(brief, labels) for brief in members]
            blocks.append("")
        return "\n".join(blocks).strip()


def _link_text(story: Story) -> str:
    """Name the site the link opens: an aggregator is how we found it, not where it goes."""
    if story.source in _AGGREGATORS:
        return urlparse(story.url).netloc.removeprefix("www.")
    return story.source


def _bullet(brief: Brief, labels: dict[str, Story]) -> str:
    stories = [labels[label] for label in dict.fromkeys(brief.sources) if label in labels]
    links = [f"[{_link_text(story)}]({story.url})" for story in stories]
    return (
        f"- **{brief.headline.strip().rstrip('.')}.** {brief.summary.strip()} {' · '.join(links)}"
    )
