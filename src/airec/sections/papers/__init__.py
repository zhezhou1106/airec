"""New papers on directions the reader describes in plain language.

A direction is a text file. Everything else is derived from it:

    plan      a model turns the description into search phrases and criteria
              (redone only when the description changes)
    retrieve  targeted arXiv searches, Semantic Scholar, Hugging Face daily papers,
              and recommendations seeded by papers accepted in earlier weeks
    judge     a fast model scores each new title and abstract against the description
              (verdicts are cached, so a paper is judged once per direction)
    gate      a stricter model applies written quality rules to the shortlist and keeps
              at most `max_papers`, possibly none
    brief     a writer condenses the survivors into a pulse and short takeaways

Retrieval lives here; judge, gate and brief in `screen`; the report text in `render`.

Nothing here reads full papers or sweeps whole arXiv categories.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from pydantic import Field

from airec.config import Strict
from airec.sections import register
from airec.sections.base import Section, SectionError, SectionResult, Window
from airec.sections.papers import render, screen
from airec.sections.papers.directions import Direction, load_directions
from airec.sections.papers.plan import SearchPlan, compile_plan, load_or_compile_plan
from airec.sections.papers.screen import (
    DEFAULT_QUALITY_RULES,
    DirectionBrief,
    DirectionScan,
    Scored,
)
from airec.sources.papers import Arxiv, Paper, SemanticScholar, hf_daily_papers

__all__ = [
    "DEFAULT_QUALITY_RULES",
    "DirectionBrief",
    "DirectionScan",
    "Papers",
    "Scored",
    "SearchPlan",
    "compile_plan",
]

_PHRASES_PER_QUERY = 6
_SCORE_TIE = 1.0  # scores this close are a tie between directions


@register
class Papers(Section):
    key = "papers"
    title = "Papers on your directions"
    schema = screen.SCHEMA

    class Config(Strict):
        min_score: float = Field(default=8.0, ge=0, le=10)  # relevance needed to reach the gate
        max_papers: int = Field(default=5, ge=1)  # most papers per direction, per week
        max_shortlist: int = Field(default=15, ge=1)  # how many the gate may consider
        quality_rules: list[str] = DEFAULT_QUALITY_RULES
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
        widened = self._widen(window)
        daily = self._daily(widened)
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
            shown += [item.paper.id for item in scan.accepted] + scan.rejected

        def commit() -> None:
            for scan in scans:
                self._remember_seeds(scan)

        return SectionResult(
            markdown="\n\n".join(blocks),
            highlights=highlights,
            shown=shown,
            stats={
                "directions": len(scans),
                "candidates": sum(scan.candidates for scan in scans),
                "relevant": sum(scan.relevant for scan in scans),
                "shown": sum(len(scan.accepted) for scan in scans),
            },
            commit=commit,
        )

    def checks(self) -> list[tuple[str, Callable[[], str]]]:
        def directions() -> str:
            found = load_directions(self.ctx.settings.paths.directions)
            return ", ".join(d.id for d in found) or "none yet"

        return [("paper directions", directions)]

    # ---- one direction ----

    def preview(self, direction: Direction, window: Window) -> DirectionScan:
        """What a run over `window` would find for one direction: the same widened window
        and the same sources, Hugging Face daily papers included. Writes only caches."""
        widened = self._widen(window)
        return self.scan(direction, widened, self._daily(widened))

    def _widen(self, window: Window) -> Window:
        """Indexes lag, so look a few days further back; judged papers stay cached."""
        return Window(window.since - timedelta(days=self.config.lookback_days), window.until)

    def _daily(self, window: Window) -> list[Paper]:
        return self.gather(
            "Hugging Face daily papers",
            lambda: hf_daily_papers(self.ctx.http, window.since, window.until),
            [],
        )

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
        scored = screen.judge(self, direction, plan, papers)
        relevant = [item for item in scored if item.score >= self.config.min_score]
        relevant.sort(key=lambda item: (item.score, item.paper.upvotes), reverse=True)
        shortlist = relevant[: self.config.max_shortlist]
        kept, rejected = screen.gate(self, direction, shortlist)
        return DirectionScan(direction, plan, len(papers), len(relevant), kept, rejected)

    def _retrieve(
        self, direction: Direction, plan: SearchPlan, window: Window, daily: list[Paper]
    ) -> dict[str, Paper]:
        batches: list[list[Paper]] = []
        phrases = plan.phrases
        limit = self.config.arxiv_per_query
        for start in range(0, len(phrases), _PHRASES_PER_QUERY):
            group = phrases[start : start + _PHRASES_PER_QUERY]
            found = self.gather(
                f"arXiv search for {direction.id}",
                lambda g=group: self.arxiv.search(
                    g, plan.categories, window.since, window.until, limit
                ),
                None,
            )
            if found is None:
                continue
            if len(found) >= limit:
                self.warn(f"{direction.id}: an arXiv search hit its limit of {limit}")
            batches.append(found)
        if not batches:
            # arXiv is the backbone: a thin report would mark this window as covered.
            raise SectionError(f"arXiv did not answer for {direction.id}")
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
        try:
            papers = self.s2.search(phrases, window.since, window.until)
            self.ctx.http.sleep(1.1)
        except Exception as exc:
            self._s2_available = False
            self.warn(f"Semantic Scholar unavailable, arXiv only: {str(exc)[:120]}")
            return []
        seeds = self.ctx.store.get_state(self.key, f"seeds:{direction.id}", [])
        if seeds:
            try:
                papers += self.s2.recommend(seeds)
                self.ctx.http.sleep(1.1)
            except Exception as exc:
                self.warn(f"recommendations for {direction.id} failed: {str(exc)[:120]}")
        return papers

    # ---- writing ----

    def _brief(self, scan: DirectionScan) -> tuple[str, str | None]:
        """A direction's block, and its top paper for the summary."""
        written = screen.brief(self, scan) if scan.accepted else None
        return render.direction_block(scan, written)

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
