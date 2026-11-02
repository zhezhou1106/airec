"""New papers on directions the reader describes in plain language.

A direction is a text file. Everything else is derived from it:

    plan      a model turns the description into search phrases and criteria
              (redone only when the description changes)
    retrieve  targeted arXiv searches, Semantic Scholar, Hugging Face daily papers,
              and recommendations seeded by papers accepted in earlier weeks
    judge     a fast model scores each new title and abstract against the description
              (verdicts are cached, so a paper is judged once per direction)
    brief     a writer condenses the best into a pulse and short takeaways

Nothing here reads full papers or sweeps whole arXiv categories.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import BaseModel, Field

from airec import prompts
from airec.config import Strict
from airec.llm import LlmError
from airec.sections import register
from airec.sections.base import Section, SectionError, SectionResult, Window
from airec.sections.papers.directions import Direction, load_directions
from airec.sections.papers.plan import SearchPlan, compile_plan, load_or_compile_plan
from airec.sources.papers import Arxiv, Paper, SemanticScholar, hf_daily_papers

__all__ = ["DirectionScan", "Papers", "SearchPlan", "compile_plan"]

_PHRASES_PER_QUERY = 6
_ABSTRACT_CHARS = 1100
_WORKERS = 4
_SCORE_TIE = 1.0  # scores this close are a tie between directions
_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_verdicts (
    direction TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    score REAL NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (direction, fingerprint, paper_id)
);
"""


class Verdict(BaseModel):
    id: str
    score: float = Field(ge=0, le=10)
    reason: str = ""


class Verdicts(BaseModel):
    verdicts: list[Verdict]


class Takeaway(BaseModel):
    id: str
    takeaway: str


class DirectionBrief(BaseModel):
    pulse: str = ""
    papers: list[Takeaway] = Field(default_factory=list)


@dataclass
class Scored:
    paper: Paper
    score: float
    reason: str
    also_in: list[str] = field(default_factory=list)


@dataclass
class DirectionScan:
    direction: Direction
    plan: SearchPlan
    candidates: int
    accepted: list[Scored]


@register
class Papers(Section):
    key = "papers"
    title = "Papers on your directions"
    schema = _SCHEMA

    class Config(Strict):
        min_score: float = Field(default=7.0, ge=0, le=10)
        max_featured: int = Field(default=5, ge=1)  # papers with a takeaway, per direction
        max_listed: int = Field(default=5, ge=0)  # further one-liners, per direction
        max_candidates: int = Field(default=200, ge=1)  # judged per direction, per run
        arxiv_per_query: int = Field(default=100, ge=1)
        judge_batch: int = Field(default=15, ge=1)
        lookback_days: int = Field(default=3, ge=0)  # indexes lag; judged papers stay cached
        max_seeds: int = Field(default=20, ge=0)  # accepted papers kept for recommendations

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self.arxiv = Arxiv(ctx.http)
        self.s2 = SemanticScholar(ctx.http, ctx.secrets.get("SEMANTIC_SCHOLAR_API_KEY"))
        self._s2_available = True

    def run(self, window: Window) -> SectionResult:
        directions = load_directions(self.ctx.settings.paths.directions)
        if not directions:
            return SectionResult(
                "No directions yet. Describe one with `rec direction add`.", stats={"directions": 0}
            )
        widened = Window(window.since - timedelta(days=self.config.lookback_days), window.until)
        daily = self.gather(
            "Hugging Face daily papers",
            lambda: hf_daily_papers(self.ctx.http, widened.since, widened.until),
            [],
        )
        scans = [self.scan(direction, widened, daily) for direction in directions]
        self.require_sources()
        _assign_to_best_direction(scans)

        blocks: list[str] = []
        highlights: list[str] = []
        shown: list[str] = []
        for scan in scans:
            block, top = self._brief(scan)
            blocks.append(block)
            if top:
                highlights.append(top)
            shown += [item.paper.id for item in self._listed(scan)]
            self._remember_seeds(scan)
        return SectionResult(
            markdown="\n\n".join(blocks),
            highlights=highlights,
            shown=shown,
            stats={
                "directions": len(scans),
                "candidates": sum(scan.candidates for scan in scans),
                "accepted": sum(len(scan.accepted) for scan in scans),
                "shown": len(set(shown)),
            },
        )

    def checks(self) -> list[tuple[str, Callable[[], str]]]:
        def directions() -> str:
            found = load_directions(self.ctx.settings.paths.directions)
            return ", ".join(d.id for d in found) or "none yet"

        return [("paper directions", directions)]

    # ---- one direction ----

    def scan(
        self, direction: Direction, window: Window, daily: list[Paper] | None = None
    ) -> DirectionScan:
        """Plan, retrieve and judge one direction. Writes only caches, never `shown`."""
        plan = load_or_compile_plan(self.ctx, self.key, direction)
        found = self._retrieve(direction, plan, window, daily or [])
        seen = self.ctx.store.shown_before(self.key, found, self.ctx.week)
        papers = [paper for paper_id, paper in found.items() if paper_id not in seen]
        papers.sort(key=lambda p: (len(p.found_by), p.upvotes, p.published_at), reverse=True)
        if len(papers) > self.config.max_candidates:
            self.warn(
                f"{direction.id}: {len(papers)} candidates, judged the top "
                f"{self.config.max_candidates}; its description may be too broad"
            )
            papers = papers[: self.config.max_candidates]
        scored = self._judge(direction, plan, papers)
        accepted = [item for item in scored if item.score >= self.config.min_score]
        accepted.sort(key=lambda item: (item.score, item.paper.upvotes), reverse=True)
        return DirectionScan(direction, plan, len(papers), accepted)

    def _retrieve(
        self, direction: Direction, plan: SearchPlan, window: Window, daily: list[Paper]
    ) -> dict[str, Paper]:
        batches: list[list[Paper]] = []
        phrases = plan.phrases
        for start in range(0, len(phrases), _PHRASES_PER_QUERY):
            group = phrases[start : start + _PHRASES_PER_QUERY]
            batches.append(
                self.gather(
                    f"arXiv search for {direction.id}",
                    lambda g=group: self.arxiv.search(
                        g, plan.categories, window.since, window.until, self.config.arxiv_per_query
                    ),
                    [],
                )
            )
        batches.append(self._semantic_scholar(direction, phrases, window))
        batches.append([paper for paper in daily if plan.matches(paper.title, paper.abstract)])

        merged: dict[str, Paper] = {}
        for paper in (paper for batch in batches for paper in batch):
            if not window.contains(paper.published_at):
                continue
            if paper.id in merged:
                merged[paper.id].absorb(paper)
            else:
                merged[paper.id] = paper
        # Daily papers carry upvotes and code links for papers arXiv found.
        for paper in daily:
            if paper.id in merged:
                merged[paper.id].absorb(paper)
        return merged

    def _semantic_scholar(
        self, direction: Direction, phrases: list[str], window: Window
    ) -> list[Paper]:
        """A bonus source: after its first refusal, leave it alone for the rest of the run."""
        if not self._s2_available:
            return []
        seeds = self.ctx.store.get_state(self.key, f"seeds:{direction.id}", [])
        try:
            papers = self.s2.search(phrases, window.since, window.until)
            self.ctx.http.sleep(1.1)
            if seeds:
                papers += self.s2.recommend(seeds)
                self.ctx.http.sleep(1.1)
        except Exception as exc:
            self._s2_available = False
            self.warn(f"Semantic Scholar unavailable, arXiv only: {str(exc)[:120]}")
            return []
        return papers

    def _judge(self, direction: Direction, plan: SearchPlan, papers: list[Paper]) -> list[Scored]:
        cached = {
            row["paper_id"]: (row["score"], row["reason"])
            for row in self.ctx.store.db.execute(
                "SELECT paper_id, score, reason FROM paper_verdicts "
                "WHERE direction = ? AND fingerprint = ?",
                (direction.id, direction.fingerprint),
            )
        }
        pending = [paper for paper in papers if paper.id not in cached]
        size = self.config.judge_batch
        batches = [pending[start : start + size] for start in range(0, len(pending), size)]
        system = prompts.render(
            "paper_judge", description=direction.description, criteria=plan.criteria_text()
        )
        failed = 0

        def judge(batch: list[Paper]) -> list[Verdict]:
            nonlocal failed
            labels = {f"p{number}": paper for number, paper in enumerate(batch, 1)}
            user = "\n\n".join(
                f"{label}: {paper.title}\n{paper.abstract[:_ABSTRACT_CHARS]}"
                for label, paper in labels.items()
            )
            try:
                reply = self.ctx.llm.json("fast", system, user, Verdicts)
            except LlmError as exc:
                failed += 1
                self.warn(f"judging for {direction.id} failed: {str(exc)[:160]}")
                return []
            return [
                Verdict(id=labels[v.id].id, score=v.score, reason=v.reason)
                for v in reply.verdicts
                if v.id in labels
            ]

        with ThreadPoolExecutor(_WORKERS) as pool:
            new = [verdict for group in pool.map(judge, batches) for verdict in group]
        if batches and failed == len(batches):
            raise SectionError(f"could not judge any paper for {direction.id}")
        self.ctx.store.executemany(
            "INSERT OR REPLACE INTO paper_verdicts VALUES (?, ?, ?, ?, ?)",
            [(direction.id, direction.fingerprint, v.id, v.score, v.reason) for v in new],
        )
        cached.update({v.id: (v.score, v.reason) for v in new})
        return [Scored(paper, *cached[paper.id]) for paper in papers if paper.id in cached]

    # ---- writing ----

    def _listed(self, scan: DirectionScan) -> list[Scored]:
        return scan.accepted[: self.config.max_featured + self.config.max_listed]

    def _brief(self, scan: DirectionScan) -> tuple[str, str | None]:
        """A direction's block, and its top paper for the summary."""
        heading = f"### {scan.plan.title}"
        funnel = f"*{scan.candidates} new candidates, {len(scan.accepted)} on target.*"
        if not scan.accepted:
            return f"{heading}\n\nNothing on target this week. {funnel}", None
        featured = scan.accepted[: self.config.max_featured]
        rest = scan.accepted[self.config.max_featured : len(self._listed(scan))]
        labels = {f"p{number}": item for number, item in enumerate(featured, 1)}
        system = prompts.render("paper_brief", description=scan.direction.description)
        user = "\n\n".join(
            f"{label}: {item.paper.title}\nWhy relevant: {item.reason}\n"
            f"{item.paper.abstract[:_ABSTRACT_CHARS]}"
            for label, item in labels.items()
        )
        brief = self.ctx.llm.json("writer", system, user, DirectionBrief)
        takeaways = {t.id: t.takeaway for t in brief.papers}

        lines = [heading, ""]
        if brief.pulse.strip():
            lines += [brief.pulse.strip(), ""]
        for label, item in labels.items():
            lines.append(_featured_line(item, takeaways.get(label) or item.reason))
        if rest:
            lines += ["", "Also relevant:", ""]
            lines += [_listed_line(item) for item in rest]
        lines += ["", funnel]
        top = featured[0]
        highlight = f"{scan.plan.title}: [{top.paper.title}]({top.paper.url})"
        return "\n".join(lines), highlight

    def _remember_seeds(self, scan: DirectionScan) -> None:
        """Accepted arXiv papers steer next week's recommendations."""
        key = f"seeds:{scan.direction.id}"
        new = [f"ArXiv:{i.paper.arxiv_id}" for i in scan.accepted if i.paper.arxiv_id]
        earlier = self.ctx.store.get_state(self.key, key, [])
        merged = list(dict.fromkeys(new + earlier))[: self.config.max_seeds]
        self.ctx.store.set_state(self.key, key, merged)


def _assign_to_best_direction(scans: list[DirectionScan]) -> None:
    """A paper appears once, under the direction with the best claim to show it.

    Near-equal scores go to the direction with fewer accepted papers: in a crowded
    direction the paper could fall past the display limit and never be seen.
    """
    claims: dict[str, list[tuple[float, DirectionScan]]] = {}
    for scan in scans:
        for item in scan.accepted:
            claims.setdefault(item.paper.id, []).append((item.score, scan))
    sizes = {id(scan): len(scan.accepted) for scan in scans}
    for scan in scans:
        kept = []
        for item in scan.accepted:
            rivals = claims[item.paper.id]
            top = max(score for score, _ in rivals)
            contenders = [claim for claim in rivals if claim[0] >= top - _SCORE_TIE]
            owner = min(contenders, key=lambda claim: sizes[id(claim[1])])[1]
            if owner is scan:
                item.also_in = [s.plan.title for _, s in rivals if s is not scan]
                kept.append(item)
        scan.accepted = kept


def _signals(item: Scored) -> str:
    paper = item.paper
    parts = [f"{item.score:.0f}/10"]
    if paper.venue:
        parts.append(paper.venue)
    if paper.upvotes:
        parts.append(f"▲{paper.upvotes}")
    if paper.code_url:
        parts.append(f"[code]({paper.code_url})")
    if item.also_in:
        parts.append(f"also: {', '.join(item.also_in)}")
    return " · ".join(parts)


def _featured_line(item: Scored, takeaway: str) -> str:
    paper = item.paper
    return f"- **[{paper.title}]({paper.url})** ({_signals(item)})  \n  {takeaway.strip()}"


def _listed_line(item: Scored) -> str:
    paper = item.paper
    return f"- [{paper.title}]({paper.url}) ({_signals(item)}): {item.reason.strip()}"
