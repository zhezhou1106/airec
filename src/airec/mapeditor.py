"""The map editor: revise the interest map with a strong model, optionally after web searches.

It never saves anything. It writes a proposal into its job folder:

    request.txt     what you asked for
    searches.json   the searches it ran and what came back
    proposal.yaml   the complete revised map (already validated)
    changes.md      what it changed and why

You review the difference on Settings → Interests and accept or discard it.
"""
from __future__ import annotations

import json
import re

from airec import interests, prompts
from airec.config import Config
from airec.phases.common import Job, PhaseError
from airec.runlog import RunContext
from airec.search import WebSearch
from airec.store import Store

YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.S)


def run_map_edit(cfg: Config, rc: RunContext, request: str, use_web: bool) -> None:
    (rc.dir / "request.txt").write_text(request, encoding="utf-8")
    current = cfg.path("interests").read_text(encoding="utf-8") \
        if cfg.path("interests").exists() else "about: \"\"\ntopics: []\n"
    store = Store(cfg.db_path)
    try:
        job = Job(cfg, store, rc)
        chat = job.chat("map_editor")
        findings = ""
        if use_web:
            rc.event("search", status="running")
            findings = _search(cfg, rc, chat, request, current)
            rc.event("search", status="done")

        rc.event("revise", status="running")
        system = prompts.render("map_editor", schema=interests.SCHEMA_DOC)
        user = f"REQUEST\n{request}\n\nCURRENT MAP\n```yaml\n{current}\n```\n"
        if findings:
            user += f"\nWHAT THE WEB SEARCHES FOUND\n{findings}\n"
        reply = chat.complete(system, user, max_tokens=8000)
        proposal, changes, errors = _parse(reply)
        if errors:
            rc.log(f"  first draft had problems: {'; '.join(errors)}; asking for a fix")
            fix = (user + f"\nYOUR PREVIOUS DRAFT\n{reply}\n\nIt has these problems: "
                   f"{'; '.join(errors)}. Return the corrected complete map the same way.")
            reply = chat.complete(system, fix, max_tokens=8000)
            proposal, changes, errors = _parse(reply)
        if errors:
            (rc.dir / "reply.txt").write_text(reply, encoding="utf-8")
            raise PhaseError("the model's map is not valid: " + "; ".join(errors))
        (rc.dir / "proposal.yaml").write_text(proposal, encoding="utf-8")
        (rc.dir / "changes.md").write_text(changes or "(no summary given)", encoding="utf-8")
        rc.event("revise", status="done")
        rc.log("  proposal ready: review it on Settings → Interests")
    finally:
        store.close()


def _search(cfg: Config, rc: RunContext, chat, request: str, current: str) -> str:  # noqa: ANN001
    search = WebSearch(cfg, log=rc.log)
    if not search.available:
        rc.log("[warn] no search backend available: revising without web search")
        return ""
    try:
        plan = chat.json(prompts.render("map_search", n=6),
                         f"REQUEST\n{request}\n\nCURRENT MAP\n{current[:6000]}")
        queries = [q for q in (plan.get("queries") or []) if isinstance(q, str)][:6]
    except Exception as exc:  # noqa: BLE001
        rc.log(f"[warn] could not plan searches: {exc}")
        return ""
    results = []
    for q in queries:
        try:
            hits = search.search(q, max_results=5)
        except Exception as exc:  # noqa: BLE001
            rc.log(f"[warn] search '{q}': {exc}")
            continue
        rc.log(f"  searched: {q} ({len(hits)} results"
               + (f" via {hits[0]['engine']}" if hits else "") + ")")
        results.append({"query": q, "results": hits})
    (rc.dir / "searches.json").write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    lines = []
    for r in results:
        lines.append(f"## {r['query']}")
        for h in r["results"]:
            lines.append(f"- {h['title']} ({h['url']}): {' '.join(h['content'].split())[:500]}")
    return "\n".join(lines)


def _parse(reply: str) -> tuple[str, str, list[str]]:
    m = YAML_BLOCK.search(reply)
    if not m:
        return "", "", ["no ```yaml block in the reply"]
    text = m.group(1).strip() + "\n"
    _data, errors = interests.parse(text)
    changes = reply[m.end():].strip()
    changes = re.sub(r"^\**CHANGES:?\**\s*", "", changes, flags=re.I).strip()
    return text, changes, errors
