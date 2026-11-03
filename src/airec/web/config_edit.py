"""Editing config.yaml from the app without losing the user's comments or layout.

The file is read and written with ruamel.yaml in round-trip mode. Only values that
changed are assigned, in place, keeping each scalar's quoting and each list's style,
so everything else in the file stays byte-identical. The merged result is checked
with airec's own models (AppConfig, each section's Config) before anything is written;
a write is atomic and keeps a timestamped backup of the previous file.

API keys are never read or returned: a provider shows only the NAME of its variable
and whether that variable is set.
"""

from __future__ import annotations

import io
import itertools
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import FoldedScalarString, LiteralScalarString, ScalarString

from airec.config import AppConfig, Paths, Secrets
from airec.sections import available

KEEP_BACKUPS = 5

# The fields each simple tab edits (other keys in the block are left alone).
FIELDS = {
    "report": [
        "title",
        "reader",
        "first_window_days",
        "max_window_days",
        "copy_to",
        "notify",
        "link",
    ],
    "schedule": ["weekday", "hour", "max_attempts"],
    "cover": ["enabled", "model", "reasoning", "timeout"],
}


class Invalid(Exception):
    """The edited configuration does not validate; nothing was written."""

    def __init__(self, errors: list[dict[str, Any]]):
        super().__init__("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in errors))
        self.errors = errors


def _null(representer: Any, _data: None) -> Any:
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


def _yaml(offset: int = 2) -> YAML:
    y = YAML()  # round-trip
    y.preserve_quotes = True
    y.width = 4096  # never re-wrap a value we write
    y.indent(mapping=2, sequence=2 + offset, offset=offset)
    y.representer.add_representer(type(None), _null)
    return y


def _offset(text: str) -> int:
    """How far this file indents "- " items under their key (0 or 2, as written)."""
    lines = text.splitlines()
    for prev, line in itertools.pairwise(lines):
        item = re.match(r"( *)- ", line)
        key = re.match(r"( *)[^\s#-][^:]*:\s*(#.*)?$", prev)
        if item and key:
            return 2 if len(item.group(1)) > len(key.group(1)) else 0
    return 2


def load(text: str) -> CommentedMap:
    doc = _yaml().load(text)
    return doc if isinstance(doc, CommentedMap) else CommentedMap()


def dump(doc: CommentedMap, like: str) -> str:
    """The document as YAML, with lists indented the way `like` (the original text) does."""
    out = io.StringIO()
    _yaml(_offset(like)).dump(doc, out)
    return out.getvalue()


def plain(node: Any) -> Any:
    """ruamel nodes as JSON-ready values."""
    if isinstance(node, dict):
        return {str(k): plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [plain(v) for v in node]
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    if isinstance(node, str):
        return str(node)
    return str(node)


# ── reading ──────────────────────────────────────────────────────────────────


def snapshot(paths: Paths) -> dict[str, Any]:
    """What the Configuration tabs show. Every block includes its defaults; a section's
    `values` are the file's values over its Config defaults (saving writes only changes)."""
    text = paths.config.read_text(encoding="utf-8")
    doc = load(text)
    raw = yaml.safe_load(text) or {}
    app = AppConfig.model_validate(raw)
    secrets = Secrets.load(paths.env)
    registry = available()
    out: dict[str, Any] = {"file": str(paths.config)}
    for part, fields in FIELDS.items():
        values = getattr(app, part).model_dump(mode="json")
        out[part] = {k: values[k] for k in fields}
    out["sections"] = [
        {
            "key": key,
            "title": registry[key].title if key in registry else key,
            "values": _section_values(registry, key, block),
        }
        for key, block in (doc.get("sections") or {}).items()
    ]
    out["models"] = {
        "providers": [
            {
                "name": name,
                "base_url": p.base_url,
                "api_key_env": p.api_key_env or "",
                "key_set": bool(secrets.get(p.api_key_env)),
            }
            for name, p in app.llm.providers.items()
        ],
        "roles": [
            {"name": name, "provider": r.provider, "model": r.model, "temperature": r.temperature}
            for name, r in app.llm.roles.items()
        ],
    }
    return out


def _section_values(registry: dict[str, Any], key: str, block: Any) -> dict[str, Any]:
    """A section's effective settings: the file's values over the section's defaults."""
    explicit = plain(block) if isinstance(block, dict) else {}
    if key not in registry:
        return explicit
    try:
        defaults = registry[key].Config().model_dump(mode="json")
    except ValidationError:  # a section whose settings have no defaults shows only the file
        return explicit
    return {"enabled": True, **defaults, **explicit}


# ── changing ─────────────────────────────────────────────────────────────────


def _styled(old: Any, new: Any) -> Any:
    """`new`, written the way the old value was (quotes, folded or literal blocks)."""
    if isinstance(new, str):
        if isinstance(old, FoldedScalarString) and "\n" not in new.rstrip("\n"):
            return _folded(new, old)
        if isinstance(old, ScalarString) and not isinstance(old, FoldedScalarString):
            return type(old)(new)
        if "\n" in new.rstrip("\n"):
            return LiteralScalarString(new)
        return new
    if isinstance(new, float) and new.is_integer() and isinstance(old, int):
        return int(new)
    return new


def _folded(text: str, like: FoldedScalarString) -> FoldedScalarString:
    """A folded block wrapped about as wide as the one it replaces."""
    marks = [0, *getattr(like, "fold_pos", [])]
    width = max((b - a for a, b in itertools.pairwise(marks)), default=0) or 90
    out = FoldedScalarString(text)
    folds, start = [], 0
    for at in (i for i, ch in enumerate(text) if ch == " "):
        nxt = text.find(" ", at + 1)
        end = nxt if nxt >= 0 else len(text.rstrip("\n"))
        if end - start > width:
            folds.append(at)
            start = at + 1
    out.fold_pos = folds  # type: ignore[attr-defined]
    return out


def _fresh(value: Any, flow: bool = False) -> Any:
    if isinstance(value, dict):
        node = CommentedMap()
        for k, v in value.items():
            node[k] = _fresh(v)
        if flow:
            node.fa.set_flow_style()
        return node
    if isinstance(value, list):
        seq = CommentedSeq(_fresh(v) for v in value)
        return seq
    return value


def _merge_row(old: CommentedMap, new: dict[str, Any]) -> None:
    for key, value in new.items():
        if key in old:
            assign(old, key, value)
        elif value not in (None, False, "", []):  # an absent key already means the default
            old[key] = _fresh(value)
    for key in [k for k in old if k not in new]:
        del old[key]


def _merge_list(old: CommentedSeq, new: list[Any]) -> None:
    flow_rows = any(isinstance(x, CommentedMap) and x.fa.flow_style() for x in old)
    for i, value in enumerate(new):
        if i < len(old):
            if isinstance(old[i], CommentedMap) and isinstance(value, dict):
                _merge_row(old[i], value)
            elif plain(old[i]) != value:
                old[i] = _styled(old[i], value)
        else:
            if isinstance(value, dict):  # a new row: leave defaults out, like the others
                value = {k: v for k, v in value.items() if v not in (None, False, "", [])}
            old.append(_fresh(value, flow=flow_rows and isinstance(value, dict)))
    while len(old) > len(new):
        del old[len(old) - 1]


def assign(node: CommentedMap, key: str, new: Any) -> None:
    """Set node[key] to `new`, touching only what differs."""
    if key not in node:
        node[key] = _fresh(new)
        return
    old = node[key]
    if plain(old) == new:
        return
    if isinstance(old, CommentedSeq) and isinstance(new, list):
        _merge_list(old, new)
    elif isinstance(old, CommentedMap) and isinstance(new, dict):
        for k, v in new.items():
            assign(old, k, v)
    else:
        node[key] = _styled(old, new)


def _block(doc: CommentedMap, *keys: str) -> CommentedMap:
    node = doc
    for key in keys:
        if not isinstance(node.get(key), CommentedMap):
            node[key] = CommentedMap()
        node = node[key]
    return node


def apply(paths: Paths, part: str, values: dict[str, Any], text: str) -> CommentedMap:
    """The file (`text`) with one tab's values applied; nothing is written."""
    doc = load(text)
    if part in FIELDS:
        current = snapshot(paths)[part]
        block = _block(doc, part)
        for key in FIELDS[part]:
            if key in values and values[key] != current[key]:
                assign(block, key, values[key])
    elif part == "sections":
        # Compared with the effective values, so a default the file leaves out stays out.
        sections = _block(doc, "sections")
        current = {s["key"]: s["values"] for s in snapshot(paths)["sections"]}
        for key, block in values.items():
            if key not in sections:
                raise Invalid([{"loc": ["sections", key], "msg": "Unknown section."}])
            if not isinstance(block, dict):
                raise Invalid([{"loc": ["sections", key], "msg": "Expected settings."}])
            for field, value in block.items():
                written = isinstance(sections[key], CommentedMap) and field in sections[key]
                if not written and value == current[key].get(field):
                    continue
                if not isinstance(sections[key], CommentedMap):
                    sections[key] = CommentedMap()
                assign(sections[key], field, value)
    elif part == "models":
        # Compared with the effective values, so a default the file leaves out stays out.
        current = snapshot(paths)["models"]
        known = {
            "providers": {p["name"]: p for p in current["providers"]},
            "roles": {r["name"]: r for r in current["roles"]},
        }
        editable = {
            "providers": ("base_url", "api_key_env"),
            "roles": ("provider", "model", "temperature"),
        }
        for kind, fields in editable.items():
            for name, entry in (values.get(kind) or {}).items():
                if name not in known[kind]:
                    raise Invalid([{"loc": ["llm", kind, name], "msg": "Unknown name."}])
                node = _block(doc, "llm", kind, name)
                for field in fields:
                    if field in entry and entry[field] != known[kind][name][field]:
                        assign(
                            node,
                            field,
                            entry[field] or None if field == "api_key_env" else entry[field],
                        )
    else:
        raise KeyError(part)
    return doc


def validate(text: str) -> None:
    """Raise Invalid with field-level errors unless airec would accept this config."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise Invalid([{"loc": [], "msg": f"Not valid YAML: {exc}"}]) from None
    try:
        app = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise Invalid(_errors(exc)) from None
    errors = []
    for name, role in app.llm.roles.items():
        if role.provider not in app.llm.providers:
            errors.append({"loc": ["llm", "roles", name, "provider"], "msg": "Unknown provider."})
    registry = available()
    for key, block in app.sections.items():
        if key not in registry:
            errors.append({"loc": ["sections", key], "msg": "Unknown section."})
            continue
        options = dict(block or {})
        options.pop("enabled", None)
        try:
            registry[key].Config.model_validate(options)
        except ValidationError as exc:
            errors += _errors(exc, ["sections", key])
    if errors:
        raise Invalid(errors)


def _errors(exc: ValidationError, prefix: list[Any] | None = None) -> list[dict[str, Any]]:
    return [{"loc": [*(prefix or []), *e["loc"]], "msg": e["msg"]} for e in exc.errors()]


def save(paths: Paths, part: str, values: dict[str, Any]) -> bool:
    """Apply, validate and write. False when nothing changed (the file is not touched)."""
    before = paths.config.read_text(encoding="utf-8")
    text = dump(apply(paths, part, values, before), like=before)
    if text == before:
        return False
    validate(text)
    write(paths.config, text, before)
    return True


def write(path: Path, text: str, before: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    n = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    backup.write_text(before, encoding="utf-8")
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    for old in sorted(path.parent.glob(f"{path.name}.bak-*"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
