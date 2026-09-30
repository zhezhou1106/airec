"""The interest map: its schema, validation, and how prompts see it.

    about: "who you are and what you care about, in your own words"
    topics:
      - id: continual-learning            # short slug, unique
        name: "Continual learning"
        priority: core                    # core | side | explore
        summary: "2-3 plain sentences"
        include: ["things that count"]
        exclude: ["things that don't"]
        queries:                          # per source; each source searches differently
          arxiv: ["test-time training"]
          github: ["topic:continual-learning"]
          web: ["new continual learning method LLM"]
        watch:
          repos: ["owner/name"]           # GitHub releases (news track)
          feeds: [{name: "...", url: "..."}]
    exclude_everywhere: ["..."]
"""
from __future__ import annotations

import re
from typing import Any

import yaml

PRIORITIES = ("core", "side", "explore")
QUERY_SOURCES = ("arxiv", "github", "web", "hackernews")
SLUG = re.compile(r"^[a-z0-9][a-z0-9\-]{1,48}$")

SCHEMA_DOC = __doc__.split("\n\n", 1)[1]


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate(data: Any) -> list[str]:
    """Human-readable problems; empty when the map is usable."""
    if not isinstance(data, dict):
        return ["the map must be a mapping with 'about' and 'topics'"]
    errors: list[str] = []
    if not isinstance(data.get("about", ""), str):
        errors.append("'about' must be text")
    topics = data.get("topics")
    if not isinstance(topics, list) or not topics:
        return errors + ["'topics' must be a non-empty list"]
    seen: set[str] = set()
    for n, t in enumerate(topics, 1):
        where = f"topic {n}"
        if not isinstance(t, dict):
            errors.append(f"{where} must be a mapping")
            continue
        tid = t.get("id")
        where = f"topic '{tid or n}'"
        if not isinstance(tid, str) or not SLUG.match(tid):
            errors.append(f"{where}: id must be a lowercase slug like 'continual-learning'")
        elif tid in seen:
            errors.append(f"{where}: id is used twice")
        seen.add(str(tid))
        if tid == "other":
            errors.append("'other' is reserved for items outside the map")
        if not isinstance(t.get("name"), str) or not t.get("name"):
            errors.append(f"{where}: needs a name")
        if t.get("priority", "core") not in PRIORITIES:
            errors.append(f"{where}: priority must be one of {', '.join(PRIORITIES)}")
        if not isinstance(t.get("summary"), str) or not t.get("summary", "").strip():
            errors.append(f"{where}: needs a summary")
        for key in ("include", "exclude"):
            if key in t and not _str_list(t[key]):
                errors.append(f"{where}: {key} must be a list of text")
        queries = t.get("queries") or {}
        if not isinstance(queries, dict):
            errors.append(f"{where}: queries must map a source to a list of queries")
        else:
            for src, qs in queries.items():
                if src not in QUERY_SOURCES:
                    errors.append(f"{where}: unknown query source '{src}' "
                                  f"(use {', '.join(QUERY_SOURCES)})")
                elif not _str_list(qs):
                    errors.append(f"{where}: queries.{src} must be a list of text")
        watch = t.get("watch") or {}
        if not isinstance(watch, dict):
            errors.append(f"{where}: watch must be a mapping")
        else:
            if "repos" in watch and not _str_list(watch["repos"]):
                errors.append(f"{where}: watch.repos must be a list like ['owner/name']")
            for feed in watch.get("feeds") or []:
                if not isinstance(feed, dict) or not feed.get("url"):
                    errors.append(f"{where}: each watch.feeds entry needs a url")
    if "exclude_everywhere" in data and not _str_list(data["exclude_everywhere"]):
        errors.append("exclude_everywhere must be a list of text")
    return errors


def parse(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [f"not valid YAML: {exc}"]
    errors = validate(data)
    return (data if not errors else None), errors


def prompt_block(data: dict[str, Any], *, detail: bool = True) -> str:
    """The map as the judging models read it."""
    lines = []
    if data.get("about"):
        lines += ["ABOUT THE READER", str(data["about"]).strip(), ""]
    lines.append("TOPICS (id — priority — what it covers)")
    for t in data.get("topics") or []:
        lines.append(f"- {t['id']} — {t.get('priority', 'core')} — {t.get('name', '')}: "
                     f"{' '.join(str(t.get('summary', '')).split())}")
        if detail:
            if t.get("include"):
                lines.append(f"    counts: {'; '.join(t['include'])}")
            if t.get("exclude"):
                lines.append(f"    does not count: {'; '.join(t['exclude'])}")
    if data.get("exclude_everywhere"):
        lines += ["", "NEVER WANTED: " + "; ".join(data["exclude_everywhere"])]
    return "\n".join(lines)


def topic_text(t: dict[str, Any]) -> str:
    """What a topic looks like to the embedding model."""
    parts = [t.get("name", ""), t.get("summary", ""), "; ".join(t.get("include") or [])]
    return "\n".join(p for p in parts if p)


def topic_names(data: dict[str, Any]) -> dict[str, str]:
    names = {t["id"]: t.get("name", t["id"]) for t in data.get("topics") or [] if "id" in t}
    names["other"] = "Outside your map"
    return names


# ---------------------------------------------------------------- pasted answers

_YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.S)


def extract_answer(text: str) -> str:
    """The map from a model's answer: its ```yaml block, or the whole text if there is none."""
    blocks = _YAML_BLOCK.findall(text or "")
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    return (text or "").strip() + "\n"


def _union(a: list[Any] | None, b: list[Any] | None) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for v in (a or []) + (b or []):
        key = (v.get("url") if isinstance(v, dict) else str(v)).strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def merge(mine: dict[str, Any], theirs: dict[str, Any]) -> dict[str, Any]:
    """Keep everything of mine and add theirs. No model involved, nothing lost.

    Same topic id: my name, summary and priority stay; include, exclude, queries
    and watch lists are combined. New topic ids are appended. 'about' stays mine
    unless mine is empty.
    """
    import copy

    out = copy.deepcopy(mine)
    if not str(out.get("about") or "").strip():
        out["about"] = theirs.get("about", "")
    by_id = {t["id"]: t for t in out.get("topics") or [] if isinstance(t, dict) and "id" in t}
    for t in theirs.get("topics") or []:
        if not isinstance(t, dict) or "id" not in t:
            continue
        cur = by_id.get(t["id"])
        if cur is None:
            out.setdefault("topics", []).append(copy.deepcopy(t))
            by_id[t["id"]] = out["topics"][-1]
            continue
        for key in ("include", "exclude"):
            if cur.get(key) or t.get(key):
                cur[key] = _union(cur.get(key), t.get(key))
        for group in ("queries", "watch"):
            theirs_g = t.get(group) or {}
            if not theirs_g:
                continue
            cur_g = cur.setdefault(group, {})
            for k, v in theirs_g.items():
                cur_g[k] = _union(cur_g.get(k), v)
    if mine.get("exclude_everywhere") or theirs.get("exclude_everywhere"):
        out["exclude_everywhere"] = _union(mine.get("exclude_everywhere"),
                                           theirs.get("exclude_everywhere"))
    return out
