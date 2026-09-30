"""Write: the article on top, the notes and mentions below, saved as markdown.

The article is written last, from the finished notes, so it can only cite
what the digest actually contains. Citations are [n]; notes are numbered
first (grouped by topic), then mentions.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from airec import interests, prompts
from airec.phases.common import Job
from airec.phases.select import Pick

CITE = re.compile(r"\[(\d{1,3})\]")


def build_refs(job: Job, sel: dict[str, list[Pick]]) -> tuple[list[dict], list[dict]]:
    names = interests.topic_names(job.cfg.interests)
    order = {t["id"]: n for n, t in enumerate(job.cfg.topics())}
    order["other"] = len(order)
    notes = sorted(sel["notes"], key=lambda p: (order.get(p.topic, 99), -p.score))
    rows = {r["id"]: r for r in job.store.items([p.id for p in notes + sel["mentions"]])}

    def ref(n: int, p: Pick) -> dict[str, Any]:
        r = rows[p.id]
        s = json.loads(r["signals"] or "{}")
        tags = []
        if s.get("code_url"):
            tags.append("code")
        if s.get("hf_upvotes"):
            tags.append(f"HF ↑{s['hf_upvotes']}")
        if s.get("stars"):
            tags.append(f"★{s['stars']}")
        if r["kind"] == "model" and s.get("device_fit"):
            tags.append(s["device_fit"])
        return {
            "n": n, "id": p.id, "title": r["title"], "url": r["url"], "kind": r["kind"],
            "source": r["source"], "authors": (r["authors"] or "")[:200],
            "published": datetime.fromtimestamp(r["published_at"] or 0).strftime("%Y-%m-%d"),
            "topic": p.topic, "topic_name": names.get(p.topic, p.topic),
            "score": p.score, "novelty": p.novelty, "reason": p.reason,
            "note": r["note"] or "", "oneliner": r["oneliner"] or p.reason,
            "back": p.back, "resurfaced": p.resurfaced, "tags": tags,
        }

    note_refs = [ref(n, p) for n, p in enumerate(notes, 1)]
    mention_refs = [ref(len(notes) + n, p) for n, p in enumerate(sel["mentions"], 1)]
    return note_refs, mention_refs


def write_article(job: Job, notes: list[dict], mentions: list[dict]) -> str:
    if not notes:
        return ""
    blocks = ["NOTES"]
    for r in notes:
        blocks.append(f"[{r['n']}] ({r['topic_name']}) {r['title']}\n{r['note']}\n")
    if mentions:
        blocks.append("MENTIONS")
        blocks += [f"[{r['n']}] ({r['topic_name']}) {r['title']} — {r['oneliner']}"
                   for r in mentions]
    words = "700 to 1000" if len(notes) >= 8 else "400 to 700"
    system = prompts.render("article", words=words,
                            topics=interests.prompt_block(job.cfg.interests, detail=False))
    text = job.chat("article").complete(system, "\n".join(blocks), max_tokens=3000)
    valid = {r["n"] for r in notes + mentions}
    text = CITE.sub(lambda m: m.group(0) if int(m.group(1)) in valid else "", text)
    text = re.sub(r"^#{1,2} .*\n+", "", text.strip())  # the digest has its own title
    return text.strip()


def digest_data(job: Job, run: Any, notes: list[dict], mentions: list[dict], article: str,
                news: dict | None) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    for r in notes:
        if not sections or sections[-1]["topic"] != r["topic"]:
            sections.append({"topic": r["topic"], "name": r["topic_name"], "notes": []})
        sections[-1]["notes"].append(r)
    since, until = run["k_since"], run["k_until"]
    return {
        "date": datetime.fromtimestamp(until).strftime("%Y-%m-%d"),
        "window": f"{_day(since)} – {_day(until)}",
        "news": news,
        "article": article,
        "sections": sections,
        "mentions": mentions,
        "counts": {"notes": len(notes), "mentions": len(mentions),
                   "news": len((news or {}).get("items", []))},
    }


def render_markdown(d: dict[str, Any], run_id: int) -> str:
    urls = {r["n"]: r["url"] for s in d["sections"] for r in s["notes"]}
    urls.update({r["n"]: r["url"] for r in d["mentions"]})

    def cite(text: str) -> str:
        return CITE.sub(lambda m: f"[{m.group(1)}]({urls[int(m.group(1))]})"
                        if int(m.group(1)) in urls else m.group(0), text)

    c = d["counts"]
    out = [
        "---",
        f"date: {d['date']}",
        f"covers: {d['window']}",
        f"run: {run_id}",
        f"notes: {c['notes']}",
        f"mentions: {c['mentions']}",
        "tags: [airec, digest]",
        "---",
        "",
        f"# Digest — {d['date']}",
        "",
        f"*{d['window']} · {c['notes']} notes · {c['mentions']} more mentioned*",
        "",
    ]
    if d.get("news"):
        out += [f"## News · {d['news']['window']}", "", d["news"]["markdown"], ""]
    if d["article"]:
        out += ["## This time", "", cite(d["article"]), ""]
    elif not d["sections"]:
        out += ["Nothing cleared the bar for a note this time.", ""]
    if d["sections"]:
        out += ["## Notes", ""]
    for s in d["sections"]:
        out += [f"### {s['name']}", ""]
        for r in s["notes"]:
            out += [f"#### {r['n']}. [{r['title']}]({r['url']})", "", _meta_line(r), ""]
            out += [r["note"], ""]
    if d["mentions"]:
        out += ["## Also worth knowing", ""]
        for r in d["mentions"]:
            out.append(f"- **{r['n']}.** [{r['title']}]({r['url']}) — {r['oneliner']} "
                       f"*({r['topic_name']})*")
        out.append("")
    return "\n".join(out)


def _meta_line(r: dict[str, Any]) -> str:
    parts = [r["published"], _source_label(r["source"]), f"`{r['score']:.1f}`"]
    parts += [f"`{t}`" for t in r["tags"]]
    line = " · ".join(parts)
    if r["back"]:
        line += f"  \n*Back again: {r['back']}*"
    elif r["resurfaced"]:
        line += "  \n*From your index*"
    return line


def _source_label(source: str) -> str:
    return {"arxiv": "arXiv", "hf_papers": "HF papers", "github": "GitHub"}.get(
        source, source.replace("feed:", ""))


def _day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%b %-d, %Y")


def save_markdown(job: Job, text: str, date: str) -> list[Path]:
    """data/digests/<date>.md, plus a copy in the Obsidian folder when one is set."""
    targets = [job.cfg.digests_dir]
    obsidian = str(job.cfg.s("output", "obsidian_dir") or "").strip()
    if obsidian:
        targets.append(Path(obsidian).expanduser())
    written = []
    for folder in targets:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{date}.md"
            n = 2
            while path.exists():
                path = folder / f"{date}-{n}.md"
                n += 1
            path.write_text(text, encoding="utf-8")
            written.append(path)
        except OSError as exc:
            job.log(f"[warn] could not write to {folder}: {exc}")
    return written
