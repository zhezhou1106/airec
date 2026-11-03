"""Writing one direction's block of the report."""

from __future__ import annotations

from airec.sections.papers.screen import DirectionBrief, DirectionScan, Scored


def direction_block(scan: DirectionScan, brief: DirectionBrief | None) -> tuple[str, str | None]:
    """A direction's block, and its top paper for the summary. `brief` is None when
    nothing was accepted."""
    heading = f"### {scan.plan.title}"
    funnel = (
        f"*{scan.candidates} new candidates, {scan.relevant} relevant, "
        f"{len(scan.accepted)} passed the quality gate.*"
    )
    if not scan.accepted or brief is None:
        return f"{heading}\n\nNothing passed the quality gate this week. {funnel}", None
    labels = {f"p{number}": item for number, item in enumerate(scan.accepted, 1)}
    takeaways = {t.id: t.takeaway for t in brief.papers}

    lines = [heading, ""]
    if brief.pulse.strip():
        lines += [brief.pulse.strip(), ""]
    for label, item in labels.items():
        lines.append(_paper_line(item, takeaways.get(label) or item.reason))
    lines += ["", funnel]
    top = scan.accepted[0]
    highlight = f"{scan.plan.title}: [{top.paper.title}]({top.paper.url})"
    return "\n".join(lines), highlight


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


def _paper_line(item: Scored, takeaway: str) -> str:
    paper = item.paper
    return f"- **[{paper.title}]({paper.url})** ({_signals(item)})  \n  {takeaway.strip()}"
