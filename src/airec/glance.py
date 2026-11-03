"""The "At a glance" opening: analysis written over the finished sections.

One writer call reads every section the report will show and returns a paragraph on
the repos, one on the news and one per paper direction. It runs after the sections
so it can connect them; if it fails, the report falls back to the plain highlights.
"""

from __future__ import annotations

from pydantic import BaseModel

from airec import prompts
from airec.llm import Llm
from airec.report import SectionOutcome

# Which section feeds which paragraph.
_REPOS, _NEWS, _PAPERS = "github_trends", "ai_news", "papers"


class DirectionTake(BaseModel):
    name: str
    paragraph: str


class Glance(BaseModel):
    repos: str = ""
    news: str = ""
    directions: list[DirectionTake] = []


def write(llm: Llm, reader: str, window: str, outcomes: list[SectionOutcome]) -> str | None:
    """Markdown for the At a glance section, or None when there is nothing to analyse."""
    shown = [o for o in outcomes if o.result and o.result.markdown.strip()]
    if not shown:
        return None
    user = "\n\n".join(f"## {o.title}\n\n{o.result.markdown.strip()}" for o in shown)  # type: ignore[union-attr]
    system = prompts.render("glance", reader=reader, window=window)
    glance = llm.json("writer", system, user, Glance)
    return render(glance, {o.key for o in shown})


def render(glance: Glance, keys: set[str]) -> str | None:
    parts = []
    if _REPOS in keys and glance.repos.strip():
        parts.append(f"**Repos.** {_flat(glance.repos)}")
    if _NEWS in keys and glance.news.strip():
        parts.append(f"**News.** {_flat(glance.news)}")
    if _PAPERS in keys:
        parts += [
            f"**{_flat(d.name)}.** {_flat(d.paragraph)}"
            for d in glance.directions
            if d.name.strip() and d.paragraph.strip()
        ]
    return "\n\n".join(parts) or None


def _flat(text: str) -> str:
    """One paragraph: a stray line break would end it early in Markdown."""
    return " ".join(text.split())
