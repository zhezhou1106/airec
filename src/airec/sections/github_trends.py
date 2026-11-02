"""New GitHub repositories gaining momentum in AI.

Candidates come from the trending page and from topic searches over recent
repos. Momentum is measured, in order of preference: GitHub's own "stars this
week", a stored star count from an earlier run, or an estimate from age. A model
then keeps what is really AI and worth a look, working from each README rather
than from the description alone.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from airec import prompts
from airec.config import Strict
from airec.sections import register
from airec.sections.base import Section, SectionError, SectionResult, Window
from airec.sources.github import GitHub, Repo

_README_CHARS = 1200
_WORKERS = 6
_SNAPSHOT_MIN_AGE_DAYS = 3
_SCHEMA = """
CREATE TABLE IF NOT EXISTS repo_stars (
    repo TEXT NOT NULL,
    day TEXT NOT NULL,
    stars INTEGER NOT NULL,
    PRIMARY KEY (repo, day)
);
"""


class RepoPick(BaseModel):
    repo: str
    summary: str
    why_now: str = ""


class RepoSelection(BaseModel):
    trend: str = ""
    picks: list[RepoPick]


@register
class GithubTrends(Section):
    key = "github_trends"
    title = "GitHub: rising repos"
    schema = _SCHEMA

    class Config(Strict):
        trending_languages: list[str] = ["", "python", "typescript", "rust"]  # "" is all
        search_topics: list[str] = ["llm", "ai-agents", "mcp", "generative-ai"]
        max_age_days: int = Field(default=120, ge=1)  # "new" means younger than this
        min_stars: int = Field(default=150, ge=0)
        max_repos: int = Field(default=10, ge=1)
        max_candidates: int = Field(default=40, ge=1)
        cooldown_weeks: int = Field(default=8, ge=0)  # before a repo may appear again
        extra_context: str = ""

    def run(self, window: Window) -> SectionResult:
        github = GitHub(self.ctx.http, self.ctx.secrets.get("GITHUB_TOKEN"))
        now = window.until
        repos = self._collect(github, now)
        self.require_sources()
        fresh = [repo for repo in repos if self._is_new(repo, now)]
        gained = {repo.id: self._momentum(repo, now) for repo in fresh}
        self._save_snapshots(fresh, now)
        candidates = self._drop_recently_shown(fresh, now)
        candidates.sort(key=lambda repo: gained[repo.id], reverse=True)
        candidates = candidates[: self.config.max_candidates]
        stats = {"seen": len(repos), "new": len(fresh), "candidates": len(candidates)}
        if not candidates:
            return SectionResult("No new repo stood out this week.", stats=stats)

        self._attach_readmes(github, candidates)
        selection = self._select(candidates, gained)
        by_name = {repo.full_name.lower(): repo for repo in candidates}
        picks = [pick for pick in selection.picks if pick.repo.lower() in by_name]
        picks = picks[: self.config.max_repos]
        if not picks:
            if selection.picks:
                raise SectionError("the model picked no real candidate")
            return SectionResult("No new AI repo stood out this week.", stats=stats)
        lines = [selection.trend.strip(), ""] if selection.trend.strip() else []
        lines += [_bullet(pick, by_name[pick.repo.lower()], gained, now) for pick in picks]
        top = picks[0]
        return SectionResult(
            markdown="\n".join(lines),
            highlights=[
                f"Rising repo: [{top.repo}]({by_name[top.repo.lower()].url}): {top.summary}"
            ],
            shown=[by_name[pick.repo.lower()].id for pick in picks],
            stats={**stats, "shown": len(picks)},
        )

    def checks(self) -> list[tuple[str, Callable[[], str]]]:
        github = GitHub(self.ctx.http, self.ctx.secrets.get("GITHUB_TOKEN"))
        return [("GitHub trending page", lambda: f"{len(github.trending())} repos parsed")]

    # ---- collecting ----

    def _collect(self, github: GitHub, now: datetime) -> list[Repo]:
        found: dict[str, Repo] = {}

        def add(repos: list[Repo]) -> None:
            for repo in repos:
                existing = found.get(repo.id)
                if existing is None:
                    found[repo.id] = repo
                elif existing.stars_gained is None:
                    existing.stars_gained = repo.stars_gained

        for language in self.config.trending_languages:
            add(
                self.gather(
                    f"trending/{language or 'all'}", lambda name=language: github.trending(name), []
                )
            )

        born = (now - timedelta(days=self.config.max_age_days)).strftime("%Y-%m-%d")
        for topic in self.config.search_topics:
            query = f"topic:{topic} created:>={born} stars:>={self.config.min_stars}"
            add(self.gather(f"search/{topic}", lambda q=query: github.search(q), []))

        # The trending page omits creation dates, topics and archive flags.
        missing = [repo for repo in found.values() if repo.created_at is None]
        with ThreadPoolExecutor(_WORKERS) as pool:
            details = pool.map(
                lambda r: self.gather(r.full_name, lambda: github.repo(r.full_name), None), missing
            )
            for repo, detail in zip(missing, details, strict=True):
                if detail is not None:
                    repo.created_at = detail.created_at
                    repo.topics = detail.topics
                    repo.archived = detail.archived
                    repo.fork = detail.fork
                    repo.stars = detail.stars or repo.stars
                    repo.description = repo.description or detail.description
        return list(found.values())

    def _is_new(self, repo: Repo, now: datetime) -> bool:
        if repo.created_at is None or repo.archived or repo.fork:
            return False
        return (now - repo.created_at).days <= self.config.max_age_days

    # ---- momentum ----

    def _momentum(self, repo: Repo, now: datetime) -> int:
        """Stars gained in about the last week."""
        if repo.stars_gained is not None:
            return repo.stars_gained
        cutoff = (now - timedelta(days=_SNAPSHOT_MIN_AGE_DAYS)).strftime("%Y-%m-%d")
        row = self.ctx.store.db.execute(
            "SELECT day, stars FROM repo_stars WHERE repo = ? AND day <= ? "
            "ORDER BY day DESC LIMIT 1",
            (repo.id, cutoff),
        ).fetchone()
        if row:
            elapsed = (now.date() - datetime.fromisoformat(row["day"]).date()).days
            return round((repo.stars - row["stars"]) * 7 / max(elapsed, 1))
        age_days = max((now - (repo.created_at or now)).days, 1)
        return round(repo.stars * min(7 / age_days, 1.0))

    def _save_snapshots(self, repos: list[Repo], now: datetime) -> None:
        day = now.strftime("%Y-%m-%d")
        self.ctx.store.executemany(
            "INSERT OR REPLACE INTO repo_stars (repo, day, stars) VALUES (?, ?, ?)",
            [(repo.id, day, repo.stars) for repo in repos],
        )

    def _drop_recently_shown(self, repos: list[Repo], now: datetime) -> list[Repo]:
        shown = self.ctx.store.shown_before(self.key, [r.id for r in repos], self.ctx.week)
        horizon = now - timedelta(weeks=self.config.cooldown_weeks)
        return [
            repo
            for repo in repos
            if repo.id not in shown or datetime.fromisoformat(shown[repo.id]) < horizon
        ]

    # ---- selecting ----

    def _attach_readmes(self, github: GitHub, repos: list[Repo]) -> None:
        with ThreadPoolExecutor(_WORKERS) as pool:
            readmes = pool.map(
                lambda r: self.gather(
                    f"{r.full_name} README", lambda: github.readme(r.full_name), ""
                ),
                repos,
            )
            for repo, readme in zip(repos, readmes, strict=True):
                repo.readme = " ".join(readme.split())[:_README_CHARS]

    def _select(self, repos: list[Repo], gained: dict[str, int]) -> RepoSelection:
        blocks = []
        for repo in repos:
            age = (datetime.now(UTC) - repo.created_at).days if repo.created_at else "?"
            blocks.append(
                f"### {repo.full_name}\n"
                f"stars: {repo.stars}; gained this week: ~{gained[repo.id]}; age: {age} days; "
                f"language: {repo.language or '?'}; topics: {', '.join(repo.topics) or '-'}\n"
                f"description: {repo.description}\nREADME: {repo.readme}"
            )
        system = prompts.render(
            "github_trends",
            reader=self.ctx.reader,
            max_repos=self.config.max_repos,
            extra=self.config.extra_context or "(none)",
        )
        return self.ctx.llm.json("writer", system, "\n\n".join(blocks), RepoSelection)


def _human(number: int) -> str:
    return f"{number / 1000:.1f}k" if number >= 1000 else str(number)


def _bullet(pick: RepoPick, repo: Repo, gained: dict[str, int], now: datetime) -> str:
    facts = [f"★ {_human(repo.stars)} (+{_human(max(gained[repo.id], 0))})"]
    if repo.language:
        facts.append(repo.language)
    if repo.created_at:
        facts.append(f"{max((now - repo.created_at).days // 7, 1)}w old")
    why = f" {pick.why_now.strip()}" if pick.why_now.strip() else ""
    heading = f"**[{repo.full_name}]({repo.url})** · {' · '.join(facts)}"
    return f"- {heading}  \n  {pick.summary.strip()}{why}"
