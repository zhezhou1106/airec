"""Settings: Interests, Sources, Models, Digest, Keys."""
from __future__ import annotations

import difflib
import io
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from ruamel.yaml import YAML

import confstore
from config import PHASES, Config
from runlog import read_meta
from search import BRAVE_KEY
from search import KEY as TAVILY_KEY
from web import envfile, jobs
from web.app import cfg, render

router = APIRouter()
TABS = [("interests", "Interests"), ("sources", "Sources"), ("models", "Models"),
        ("digest", "Digest"), ("keys", "Keys")]
FILE_TABS = {"interests", "sources", "models"}


def _page(request: Request, tab: str, c: Config, **ctx: Any) -> HTMLResponse:
    return render(request, f"settings/{tab}.html", cfg=c, nav="settings", tabs=TABS, tab=tab,
                  **ctx)


@router.get("/settings")
def settings_home() -> RedirectResponse:
    return RedirectResponse("/settings/interests", status_code=303)


@router.get("/settings/{tab}", response_class=HTMLResponse)
def settings_tab(request: Request, tab: str, saved: str = "", error: str = "") -> HTMLResponse:
    c = cfg()
    if tab not in dict(TABS):
        raise HTTPException(404, "no such settings page")
    ctx: dict[str, Any] = {"saved": saved, "error": error}
    if tab in FILE_TABS:
        path = c.path(tab)
        ctx["text"] = path.read_text(encoding="utf-8") if path.exists() else ""
        ctx["versions"] = confstore.versions(c, tab)[:30]
        ctx["errors"] = []
    if tab == "interests":
        ctx["proposal"] = _latest_proposal(c)
        ctx["topics"] = c.topics()
        ctx["copy_prompt"] = _copy_prompt(c)
        ctx["draft"] = _draft_preview(c)
    if tab == "models":
        ctx["phases"] = PHASES
    if tab == "digest":
        ctx["s"] = c.settings
    if tab == "keys":
        ctx["keys"] = _keys(c)
    return _page(request, tab, c, **ctx)


# ---------------------------------------------------------------- YAML files

@router.post("/settings/file/{name}", response_class=HTMLResponse)
def save_file(request: Request, name: str, text: str = Form(...)) -> HTMLResponse:
    c = cfg()
    if name not in FILE_TABS:
        raise HTTPException(404, "no such file")
    try:
        confstore.save(c, name, text.replace("\r\n", "\n"), reason="edited in Settings")
    except confstore.SaveError as exc:
        return _page(request, name, c, text=text, errors=exc.errors,
                     versions=confstore.versions(c, name)[:30], saved="", error="",
                     proposal=_latest_proposal(c) if name == "interests" else None,
                     topics=c.topics(), phases=PHASES, copy_prompt=_copy_prompt(c),
                     draft=_draft_preview(c))
    return RedirectResponse(f"/settings/{name}?saved=1", status_code=303)


@router.get("/settings/file/{name}/version/{stamp}", response_class=PlainTextResponse)
def view_version(name: str, stamp: str) -> PlainTextResponse:
    try:
        return PlainTextResponse(confstore.version_text(cfg(), name, stamp))
    except FileNotFoundError as exc:
        raise HTTPException(404, "no such version") from exc


@router.post("/settings/file/{name}/restore/{stamp}")
def restore_version(name: str, stamp: str) -> RedirectResponse:
    c = cfg()
    try:
        text = confstore.version_text(c, name, stamp)
        confstore.save(c, name, text, reason=f"restored {stamp}")
    except FileNotFoundError as exc:
        raise HTTPException(404, "no such version") from exc
    except confstore.SaveError as exc:
        return RedirectResponse(f"/settings/{name}?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/settings/{name}?saved=1", status_code=303)


# ---------------------------------------------------------------- copy / paste with a strong model

def _copy_prompt(c: Config) -> str:
    import interests, prompts

    current = c.path("interests").read_text(encoding="utf-8") if c.path("interests").exists() \
        else "about: \"\"\ntopics: []\n"
    return prompts.render("map_copy", schema=interests.SCHEMA_DOC, current=current.strip())


def _draft_path(c: Config) -> Path:
    return c.data_dir / "drafts" / "interests.yaml"


def _draft_preview(c: Config) -> dict[str, Any] | None:
    import yaml

    import interests

    path = _draft_path(c)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    theirs = yaml.safe_load(text)
    current_text = _read(c.path("interests"))
    mine = yaml.safe_load(current_text) or {}
    merged_text = _dump(interests.merge(mine, theirs))
    mine_ids = {t.get("id") for t in mine.get("topics") or []}
    their_ids = {t.get("id") for t in theirs.get("topics") or []}
    return {
        "replace": _diff(current_text, text),
        "merge": _diff(current_text, merged_text),
        "new_topics": sorted(their_ids - mine_ids),
        "dropped_topics": sorted(mine_ids - their_ids),
        "changes": _read(path.with_suffix(".notes.md")),
    }


def _dump(data: dict[str, Any]) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


@router.post("/settings/interests/paste", response_class=HTMLResponse)
def paste_answer(request: Request, answer: str = Form("")) -> HTMLResponse:
    import interests

    c = cfg()
    text = interests.extract_answer(answer)
    data, errors = interests.parse(text)
    if errors:
        return _page(request, "interests", c, text=_read(c.path("interests")), errors=[],
                     versions=confstore.versions(c, "interests")[:30], saved="", error="",
                     proposal=_latest_proposal(c), topics=c.topics(),
                     copy_prompt=_copy_prompt(c), draft=None, paste=answer,
                     paste_errors=errors)
    path = _draft_path(c)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    after = answer.split("```")[-1].strip() if "```" in answer else ""
    path.with_suffix(".notes.md").write_text(after, encoding="utf-8")
    return RedirectResponse("/settings/interests#draft", status_code=303)


@router.post("/settings/interests/draft/apply")
def apply_draft(mode: str = Form("replace")) -> RedirectResponse:
    import yaml

    import interests

    c = cfg()
    path = _draft_path(c)
    if not path.exists():
        return RedirectResponse("/settings/interests", status_code=303)
    text = path.read_text(encoding="utf-8")
    if mode == "merge":
        mine = yaml.safe_load(_read(c.path("interests"))) or {}
        text = _dump(interests.merge(mine, yaml.safe_load(text)))
    try:
        confstore.save(c, "interests", text, reason=f"pasted answer ({mode})")
    except confstore.SaveError as exc:
        return RedirectResponse(f"/settings/interests?error={quote(str(exc))}", status_code=303)
    path.unlink()
    path.with_suffix(".notes.md").unlink(missing_ok=True)
    return RedirectResponse("/settings/interests?saved=1", status_code=303)


@router.post("/settings/interests/draft/discard")
def discard_draft() -> RedirectResponse:
    path = _draft_path(cfg())
    path.unlink(missing_ok=True)
    path.with_suffix(".notes.md").unlink(missing_ok=True)
    return RedirectResponse("/settings/interests", status_code=303)


# ---------------------------------------------------------------- map editor

def _latest_proposal(c: Config) -> dict[str, Any] | None:
    for j in jobs.list_jobs(c, limit=40):
        if j["kind"] != "revise":
            continue
        d = c.logs_dir / j["name"]
        if (d / "applied").exists() or (d / "discarded").exists():
            return None
        return _proposal(c, j["name"])
    return None


def _proposal(c: Config, name: str) -> dict[str, Any]:
    d = jobs.job_dir(c, name)
    meta = read_meta(d)
    out: dict[str, Any] = {"name": name, "status": meta.get("status"),
                           "request": _read(d / "request.txt"), "error": meta.get("error", ""),
                           "log": jobs.tail(d / "run.log", 12)}
    proposal = d / "proposal.yaml"
    if proposal.exists():
        current = _read(c.path("interests"))
        new = proposal.read_text(encoding="utf-8")
        out["changes"] = _read(d / "changes.md")
        out["diff"] = _diff(current, new)
        out["ready"] = True
    return out


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _diff(old: str, new: str) -> list[dict[str, str]]:
    rows = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), "current", "proposed",
                                     n=2, lineterm=""):
        if line.startswith(("---", "+++")):
            continue
        kind = {"+": "add", "-": "del", "@": "hunk"}.get(line[:1], "ctx")
        rows.append({"kind": kind, "text": line[1:] if kind in ("add", "del", "ctx") else line})
    return rows


@router.post("/settings/interests/revise")
def revise(request_text: str = Form(..., alias="request"), web: str = Form("")) -> RedirectResponse:
    c = cfg()
    if not request_text.strip():
        return RedirectResponse("/settings/interests?error=" + quote("Say what to change."),
                                status_code=303)
    missing = c.missing_key("map_editor") if "map_editor" in (c.models.get("phases") or {}) \
        else "a map_editor model in models.yaml"
    if missing:
        return RedirectResponse("/settings/interests?error=" + quote(
            f"The map editor needs {missing} (Settings → Keys)."), status_code=303)
    tmp = c.data_dir / "revise-request.txt"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(request_text, encoding="utf-8")
    argv = ["--request-file", str(tmp), "--web" if web else "--no-web"]
    try:
        jobs.start(c, "revise", argv)
    except jobs.JobError as exc:
        return RedirectResponse(f"/settings/interests?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/settings/interests", status_code=303)


@router.get("/settings/interests/proposal/{name}", response_class=HTMLResponse)
def proposal_panel(request: Request, name: str) -> HTMLResponse:
    c = cfg()
    try:
        p = _proposal(c, name)
    except jobs.JobError as exc:
        raise HTTPException(404, str(exc)) from exc
    return render(request, "settings/_proposal.html", cfg=c, proposal=p)


@router.post("/settings/interests/proposal/{name}/apply")
def apply_proposal(name: str) -> RedirectResponse:
    c = cfg()
    d = jobs.job_dir(c, name)
    request_text = _read(d / "request.txt").strip().splitlines()
    reason = "revision: " + (request_text[0][:80] if request_text else name)
    try:
        confstore.save(c, "interests", _read(d / "proposal.yaml"), reason=reason)
    except confstore.SaveError as exc:
        return RedirectResponse(f"/settings/interests?error={quote(str(exc))}", status_code=303)
    (d / "applied").write_text("")
    return RedirectResponse("/settings/interests?saved=1", status_code=303)


@router.post("/settings/interests/proposal/{name}/discard")
def discard_proposal(name: str) -> RedirectResponse:
    (jobs.job_dir(cfg(), name) / "discarded").write_text("")
    return RedirectResponse("/settings/interests", status_code=303)


# ---------------------------------------------------------------- models

@router.post("/settings/models/test", response_class=HTMLResponse)
def test_models(request: Request) -> HTMLResponse:
    from llm import ChatClient, EmbedClient

    c = cfg()
    rows = []
    tested: dict[tuple[str, str], str] = {}
    for phase in PHASES:
        conf = (c.models.get("phases") or {}).get(phase)
        if not conf:
            rows.append({"phase": phase, "model": "", "result": "not set", "ok": False})
            continue
        missing = c.missing_key(phase)
        if missing:
            rows.append({"phase": phase, "model": conf.get("model", ""),
                         "result": f"needs {missing}", "ok": False})
            continue
        ep = c.endpoint(phase)
        key = (ep.base_url, ep.model)
        if key not in tested:
            try:
                tested[key] = f"answered in {ChatClient(ep).ping(timeout=60)} s"
            except Exception as exc:  # noqa: BLE001
                tested[key] = f"failed: {exc}"
        rows.append({"phase": phase, "model": f"{ep.model} · {ep.provider}",
                     "result": tested[key], "ok": not tested[key].startswith("failed")})
    try:
        EmbedClient(c).embed(["ping"])
        rows.append({"phase": "embedding", "model": c.embedding().model, "result": "ok",
                     "ok": True})
    except Exception as exc:  # noqa: BLE001
        rows.append({"phase": "embedding", "model": c.embedding().model,
                     "result": f"failed: {exc}", "ok": False})
    return render(request, "settings/_model_test.html", cfg=c, rows=rows)


# ---------------------------------------------------------------- digest

DIGEST_FIELDS: list[tuple[str, str, type]] = [
    ("digest", "notes", int), ("digest", "mentions", int),
    ("digest", "min_score_note", float), ("digest", "min_score_mention", float),
    ("digest", "max_notes_per_topic", int), ("digest", "explore_share", float),
    ("news", "max_days", int), ("news", "stories", int),
    ("run", "first_run_days", int), ("run", "resurface", int), ("run", "triage_max", int),
    ("index", "backfill_months", int), ("device", "memory_gb", int),
]


@router.post("/settings/digest")
async def save_digest(request: Request) -> RedirectResponse:
    c = cfg()
    form = await request.form()
    yaml = YAML()
    yaml.preserve_quotes = True
    path = c.path("settings")
    data = yaml.load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    try:
        for section, key, cast in DIGEST_FIELDS:
            raw = str(form.get(f"{section}.{key}", "")).strip()
            if raw:
                data.setdefault(section, {})[key] = cast(raw)
    except ValueError:
        return RedirectResponse("/settings/digest?error=" + quote("Numbers only, please."),
                                status_code=303)
    data.setdefault("news", {})["enabled"] = bool(form.get("news.enabled"))
    data.setdefault("output", {})["obsidian_dir"] = str(form.get("output.obsidian_dir", "")).strip()
    data.setdefault("device", {})["name"] = str(form.get("device.name", "")).strip()
    buf = io.StringIO()
    yaml.dump(data, buf)
    try:
        confstore.save(c, "settings", buf.getvalue(), reason="edited in Settings → Digest")
    except confstore.SaveError as exc:
        return RedirectResponse(f"/settings/digest?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/settings/digest?saved=1", status_code=303)


# ---------------------------------------------------------------- keys

def _keys(c: Config) -> list[dict[str, str]]:
    wanted: dict[str, str] = {}
    for name, p in (c.models.get("providers") or {}).items():
        if isinstance(p, dict) and p.get("api_key_env"):
            wanted.setdefault(p["api_key_env"], f"for the {name} provider")
    wanted.setdefault(TAVILY_KEY, "web search, first choice (map editor, web news)")
    wanted.setdefault(BRAVE_KEY, "web search, used when Tavily fails or has no key")
    wanted.setdefault("SEMANTIC_SCHOLAR_API_KEY", "steadier topic searches (works without it)")
    wanted.setdefault("GITHUB_TOKEN", "higher GitHub rate limits")
    return [{"key": k, "use": use, "masked": envfile.mask(c.env.get(k, ""))}
            for k, use in wanted.items()]


@router.post("/settings/keys")
def save_key(key: str = Form(...), value: str = Form("")) -> RedirectResponse:
    c = cfg()
    if key not in {k["key"] for k in _keys(c)}:
        raise HTTPException(400, "unknown key")
    try:
        envfile.set_key(c.env_path, key, value.strip())
    except ValueError as exc:
        return RedirectResponse(f"/settings/keys?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/settings/keys?saved=1", status_code=303)
