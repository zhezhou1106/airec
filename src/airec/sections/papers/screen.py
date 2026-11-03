"""Screening one direction's candidates: judge, gate, brief.

judge  a fast model scores each new title and abstract against the description
       (verdicts are cached in `paper_verdicts`, so a paper is judged once per direction)
gate   a stricter model applies written quality rules to the shortlist
brief  a writer condenses the survivors into a pulse and short takeaways
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from airec import prompts
from airec.llm import LlmError
from airec.sections.base import SectionError
from airec.sections.papers.directions import Direction
from airec.sections.papers.plan import SearchPlan
from airec.sources.papers import Paper

if TYPE_CHECKING:
    from airec.sections.papers import Papers

ABSTRACT_CHARS = 1100
_WORKERS = 4
_MAX_FAILED_BATCHES = 4  # one in this many may fail before the section does
DEFAULT_QUALITY_RULES = [
    "It reports a concrete new method, mechanism, dataset or finding. Surveys, position papers, "
    "proposals and system descriptions without evaluation fail.",
    "The abstract shows real evidence: experiments, a proof, or a controlled comparison, with "
    "named baselines or concrete results. Vague claims of improvement fail.",
    "The contribution is the idea, not the application: a known method applied to one domain "
    "fails.",
    "It adds something new. A small tweak of a known method with a minor benchmark gain fails.",
    "When two papers are otherwise equal, prefer released code, then the more careful evaluation.",
]
SCHEMA = """
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


class GateDecision(BaseModel):
    id: str
    keep: bool
    reason: str = ""


class GateResult(BaseModel):
    decisions: list[GateDecision]


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
    relevant: int  # past the relevance screen
    accepted: list[Scored]  # past the quality gate
    rejected: list[str]  # ids the gate turned down; not worth offering again


def gate(
    section: Papers, direction: Direction, shortlist: list[Scored]
) -> tuple[list[Scored], list[str]]:
    """Apply the quality rules. If this cannot run, the section fails: never pass unchecked."""
    if not shortlist:
        return [], []
    labels = {f"p{number}": item for number, item in enumerate(shortlist, 1)}
    rules = "\n".join(f"{n}. {rule}" for n, rule in enumerate(section.config.quality_rules, 1))
    system = prompts.render(
        "paper_gate",
        description=direction.description,
        rules=rules,
        max_papers=section.config.max_papers,
    )
    user = "\n\n".join(
        f"{label}: {item.paper.title}\n{_evidence(item.paper)}\n"
        f"{item.paper.abstract[:ABSTRACT_CHARS]}"
        for label, item in labels.items()
    )
    result = section.ctx.llm.json("writer", system, user, GateResult)
    kept = []
    for decision in result.decisions:
        item = labels.get(decision.id)
        if decision.keep and item is not None and item not in kept:
            item.reason = decision.reason.strip() or item.reason
            kept.append(item)
    kept = kept[: section.config.max_papers]
    return kept, [item.paper.id for item in shortlist if item not in kept]


def judge(
    section: Papers, direction: Direction, plan: SearchPlan, papers: list[Paper]
) -> list[Scored]:
    """Score each paper against the description; verdicts are cached per description."""
    cached = {
        row["paper_id"]: (row["score"], row["reason"])
        for row in section.ctx.store.db.execute(
            "SELECT paper_id, score, reason FROM paper_verdicts "
            "WHERE direction = ? AND fingerprint = ?",
            (direction.id, direction.fingerprint),
        )
    }
    pending = [paper for paper in papers if paper.id not in cached]
    size = section.config.judge_batch
    batches = [pending[start : start + size] for start in range(0, len(pending), size)]
    system = prompts.render(
        "paper_judge", description=direction.description, criteria=plan.criteria_text()
    )

    def judge(batch: list[Paper]) -> list[Verdict] | None:
        """The batch's verdicts, or None when the model failed (counted after the map)."""
        labels = {f"p{number}": paper for number, paper in enumerate(batch, 1)}
        user = "\n\n".join(
            f"{label}: {paper.title}\n{paper.abstract[:ABSTRACT_CHARS]}"
            for label, paper in labels.items()
        )
        try:
            reply = section.ctx.llm.json("fast", system, user, Verdicts)
        except LlmError as exc:
            section.warn(f"judging for {direction.id} failed: {str(exc)[:160]}")
            return None
        return [
            Verdict(id=labels[v.id].id, score=v.score, reason=v.reason)
            for v in reply.verdicts
            if v.id in labels
        ]

    with ThreadPoolExecutor(_WORKERS) as pool:
        replies = list(pool.map(judge, batches))
    failed = sum(reply is None for reply in replies)
    new = [verdict for reply in replies if reply for verdict in reply]
    if failed * _MAX_FAILED_BATCHES > len(batches):
        # Unjudged papers would leave the window for good, so this week must be retried.
        raise SectionError(f"{failed} of {len(batches)} judge batches failed: {direction.id}")
    if len(new) < sum(len(batch) for batch in batches) - failed * size:
        section.warn(f"{direction.id}: the judge skipped some papers")
    section.ctx.store.executemany(
        "INSERT OR REPLACE INTO paper_verdicts VALUES (?, ?, ?, ?, ?)",
        [(direction.id, direction.fingerprint, v.id, v.score, v.reason) for v in new],
    )
    cached.update({v.id: (v.score, v.reason) for v in new})
    return [Scored(paper, *cached[paper.id]) for paper in papers if paper.id in cached]


def brief(section: Papers, scan: DirectionScan) -> DirectionBrief:
    """The writer's pulse and one takeaway per accepted paper (labelled p1, p2, ...)."""
    labels = {f"p{number}": item for number, item in enumerate(scan.accepted, 1)}
    system = prompts.render("paper_brief", description=scan.direction.description)
    user = "\n\n".join(
        f"{label}: {item.paper.title}\nWhy relevant: {item.reason}\n"
        f"{item.paper.abstract[:ABSTRACT_CHARS]}"
        for label, item in labels.items()
    )
    return section.ctx.llm.json("writer", system, user, DirectionBrief)


def _evidence(paper: Paper) -> str:
    facts = []
    if paper.venue:
        facts.append(f"venue: {paper.venue}")
    if paper.upvotes:
        facts.append(f"community upvotes: {paper.upvotes}")
    facts.append("code released" if paper.code_url else "no code link")
    return "; ".join(facts)
