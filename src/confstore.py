"""Saving config files: validate first, keep every previous version.

Each save writes data/history/<name>/<timestamp>.yaml with the version being
replaced, so any edit (by hand or by the map editor) can be undone.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

import interests
from config import CONFIG_FILES, PHASES, Config

STAMP = re.compile(r"^\d{8}-\d{6}(-\d+)?$")


class SaveError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate(name: str, text: str) -> list[str]:
    if name not in CONFIG_FILES:
        return [f"unknown config file {name}"]
    if name == "interests":
        return interests.parse(text)[1]
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return [f"not valid YAML: {exc}"]
    if not isinstance(data, dict):
        return ["must be a mapping at the top level"]
    if name == "models":
        return _validate_models(data)
    if name == "sources":
        return _validate_sources(data)
    return _validate_settings(data)


def _validate_models(data: dict[str, Any]) -> list[str]:
    errors = []
    providers = data.get("providers") or {}
    if not isinstance(providers, dict) or not providers:
        errors.append("providers: define at least one (name → base_url)")
        providers = {}
    for name, p in providers.items():
        if not isinstance(p, dict) or not p.get("base_url"):
            errors.append(f"provider '{name}' needs a base_url")
    phases = data.get("phases") or {}
    for phase in ("triage", "score", "note", "mention", "article"):
        if phase not in phases:
            errors.append(f"phases: '{phase}' needs a model")
    for phase, conf in phases.items():
        if phase not in PHASES:
            errors.append(f"unknown phase '{phase}' (phases: {', '.join(PHASES)})")
            continue
        if not isinstance(conf, dict) or not conf.get("model"):
            errors.append(f"phase '{phase}' needs a model")
        elif conf.get("provider") not in providers:
            errors.append(f"phase '{phase}' uses unknown provider '{conf.get('provider')}'")
    emb = data.get("embedding") or {}
    if emb and not (isinstance(emb, dict) and emb.get("base_url") and emb.get("model")):
        errors.append("embedding needs base_url and model")
    return errors


def _validate_sources(data: dict[str, Any]) -> list[str]:
    from sources import REGISTRY

    errors = []
    for name, conf in data.items():
        if name not in REGISTRY:
            errors.append(f"unknown source '{name}' (known: {', '.join(sorted(REGISTRY))})")
        elif not isinstance(conf, dict):
            errors.append(f"source '{name}' must be a mapping")
    for feed in (data.get("feeds") or {}).get("list", []) if isinstance(data.get("feeds"), dict) \
            else []:
        if not isinstance(feed, dict) or not feed.get("url"):
            errors.append("each feed needs a url")
        elif feed.get("track", "knowledge") not in ("knowledge", "news"):
            errors.append(f"feed {feed.get('name', feed['url'])}: track is knowledge or news")
    return errors


def _validate_settings(data: dict[str, Any]) -> list[str]:
    errors = []
    numbers = {
        "digest": ("notes", "mentions", "min_score_note", "min_score_mention",
                   "max_notes_per_topic", "explore_share"),
        "news": ("max_days", "stories"),
        "run": ("first_run_days", "triage_batch", "score_batch", "triage_max", "resurface",
                "read_chars"),
    }
    for section, keys in numbers.items():
        for key in keys:
            value = (data.get(section) or {}).get(key)
            if value is not None and (isinstance(value, bool)
                                      or not isinstance(value, (int, float)) or value < 0):
                errors.append(f"{section}.{key} must be a non-negative number")
    order = (data.get("search") or {}).get("order")
    if order is not None:
        from search import KEYS
        if not isinstance(order, list) or not order or any(o not in KEYS for o in order):
            errors.append(f"search.order must list some of: {', '.join(KEYS)}")
    days = (data.get("news") or {}).get("max_days")
    if isinstance(days, (int, float)) and not 1 <= days <= 31:
        errors.append("news.max_days must be between 1 and 31")
    return errors


def save(cfg: Config, name: str, text: str, *, reason: str = "") -> Path | None:
    """Validate and write config/<name>.yaml; returns the snapshot of the old one."""
    errors = validate(name, text)
    if errors:
        raise SaveError(errors)
    path = cfg.path(name)
    snapshot = None
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if old == text:
            return None
        snapshot = _snapshot(cfg, name, old, reason)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    cfg.reload()
    return snapshot


def _snapshot(cfg: Config, name: str, text: str, reason: str) -> Path:
    folder = cfg.history_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"{stamp}.yaml"
    n = 2
    while target.exists():
        target = folder / f"{stamp}-{n}.yaml"
        n += 1
    header = f"# replaced {datetime.now():%Y-%m-%d %H:%M}" + (f" — {reason}" if reason else "")
    target.write_text(header + "\n" + text, encoding="utf-8")
    return target


def versions(cfg: Config, name: str) -> list[dict[str, Any]]:
    folder = cfg.history_dir / name
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("*.yaml"), reverse=True):
        first = p.read_text(encoding="utf-8").split("\n", 1)[0]
        out.append({"stamp": p.stem, "label": first.lstrip("# ").strip(),
                    "when": datetime.strptime(p.stem[:15], "%Y%m%d-%H%M%S")})
    return out


def version_text(cfg: Config, name: str, stamp: str) -> str:
    if not STAMP.match(stamp) or name not in CONFIG_FILES:
        raise FileNotFoundError(stamp)
    text = (cfg.history_dir / name / f"{stamp}.yaml").read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    return rest if first.startswith("# replaced") else text
