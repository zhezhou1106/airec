"""Read: fetch the text of each selected item and write its reader's note.

Papers: the arXiv HTML version, keeping the parts that carry the decision
(end of the introduction, method, results, conclusion, figure captions).
Repos: README and the latest release notes. Posts and pages: the main text.
Text is saved once under data/library/ and reused by later notes and deep reads.

A note is four short labelled paragraphs of plain text, not JSON: small local
models are far more consistent with prose. Notes are cached per item and model.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from airec import prompts
from airec.config import USER_AGENT, Config
from airec.phases.common import (KIND_WORD, Job, PhaseError, batches, parse_lines, short_ids,
                                 signals_line)

SECTION = re.compile(r"(introduction|contribution|method|approach|model|algorithm|experiment|"
                     r"result|evaluation|analysis|ablation|conclusion|discussion|limitation)",
                     re.I)
LABELS = ("**What it is.**", "**How it works.**", "**What they found.**", "**Open it if")
SAVE_BUDGET = 60000


def library_path(cfg: Config, item_id: str, kind: str, suffix: str = "") -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", item_id)
    return cfg.library_dir / f"{kind}s" / f"{safe}{suffix}.md"


class Reader:
    def __init__(self, cfg: Config, log=print):  # noqa: ANN001
        self.cfg = cfg
        self.log = log
        self.http = httpx.Client(timeout=40, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT})

    def text(self, row: Any, store: Any, need: int) -> str:
        """Up to `need` chars of the item's text, from the library when saved before."""
        if row["text_path"]:
            path = self.cfg.abs(row["text_path"])
            if path.exists():
                return path.read_text(encoding="utf-8")[:need]
        text = self._fetch(row)
        if text:
            path = library_path(self.cfg, row["id"], row["kind"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text[:SAVE_BUDGET], encoding="utf-8")
            store.update_item(row["id"], text_path=self.cfg.rel(path))
            store.commit()
        return text[:need]

    def _fetch(self, row: Any) -> str:
        item_id = row["id"]
        try:
            if item_id.startswith("arxiv:"):
                return self._arxiv(item_id.split(":", 1)[1])
            if item_id.startswith("gh:"):
                return self._repo(item_id.split(":", 1)[1])
            if item_id.startswith("hf:"):
                return self._hf_model(item_id.split(":", 1)[1])
            return self._page(row["url"])
        except Exception as exc:  # noqa: BLE001
            self.log(f"[warn] could not read {item_id}: {exc}")
            return ""

    def _arxiv(self, arxiv_id: str) -> str:
        from bs4 import BeautifulSoup

        for url in (f"https://arxiv.org/html/{arxiv_id}",
                    f"https://arxiv.org/html/{arxiv_id}v1"):
            try:
                resp = self.http.get(url)
            except httpx.HTTPError:
                continue
            if resp.status_code != 200 or len(resp.text) < 2000:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(["script", "style", "nav", "footer", "math"]):
                tag.decompose()
            chunks: list[str] = []
            for section in soup.find_all("section"):
                heading = section.find(["h2", "h3"], recursive=False)
                if not heading:
                    continue
                name = heading.get_text(" ", strip=True)
                if not SECTION.search(name):
                    continue
                body = " ".join(section.get_text(" ", strip=True).split())
                if len(body) < 200:
                    continue
                keep = 5000
                body = body[-keep:] if re.search("introduction", name, re.I) else body[:keep]
                chunks.append(f"## {name}\n{body}")
            captions = [" ".join(c.get_text(" ", strip=True).split())[:400]
                        for c in soup.find_all("figcaption")[:10]]
            if captions:
                chunks.append("## Figure and table captions\n" + "\n".join(captions))
            if chunks:
                return "\n\n".join(chunks)
        return ""

    def _repo(self, full_name: str) -> str:
        token = self.cfg.secret("GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github.raw"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        parts = []
        resp = self.http.get(f"https://api.github.com/repos/{full_name}/readme", headers=headers)
        if resp.status_code == 200:
            parts.append("## README\n" + resp.text[:30000])
        resp = self.http.get(f"https://api.github.com/repos/{full_name}/releases",
                             params={"per_page": 2},
                             headers={**headers, "Accept": "application/vnd.github+json"})
        if resp.status_code == 200:
            for rel in resp.json():
                parts.append(f"## Release {rel.get('tag_name', '')}\n"
                             f"{(rel.get('body') or '')[:2000]}")
        return "\n\n".join(parts)

    def _hf_model(self, model_id: str) -> str:
        resp = self.http.get(f"https://huggingface.co/{model_id}/raw/main/README.md")
        return resp.text[:30000] if resp.status_code == 200 else ""

    def _page(self, url: str) -> str:
        import trafilatura

        if not url:
            return ""
        resp = self.http.get(url)
        resp.raise_for_status()
        return trafilatura.extract(resp.text, include_comments=False, include_tables=True) or ""


# ---------------------------------------------------------------- notes

def write_notes(job: Job, picks: list[Any]) -> None:
    chat = job.chat("note")
    reader = Reader(job.cfg, job.log)
    need = int(job.cfg.s("run", "read_chars"))
    rows = {r["id"]: r for r in job.store.items([p.id for p in picks])}
    failures = 0
    for n, pick in enumerate(picks, 1):
        row = rows[pick.id]
        if row["note"] and row["note_model"] == chat.model:
            job.event("read", done=n, total=len(picks))
            continue
        job.event("read", done=n - 1, total=len(picks), current=row["title"], model=chat.model)
        text = reader.text(row, job.store, need)
        kind = KIND_WORD.get(row["kind"], "item")
        system = prompts.render("note", kind=kind, topic=job.topic_line(pick.topic))
        user = "\n".join(filter(None, [
            f"Title: {row['title']}",
            f"Authors: {(row['authors'] or '')[:300]}" if row["authors"] else "",
            f"Signals: {signals_line(row)}" if signals_line(row) else "",
            f"Summary: {row['abstract'] or ''}",
            "",
            "SOURCE TEXT:" if text else "SOURCE TEXT: (not available; work from the summary)",
            text,
        ]))
        note, error = "", ""
        for _attempt in range(2):
            try:
                note = chat.complete(system, user, max_tokens=900).strip()
            except Exception as exc:  # noqa: BLE001
                note, error = "", str(exc)
                break
            if all(label in note for label in LABELS):
                break
        if error:
            failures += 1
            job.log(f"[warn] note failed for {pick.id}: {error}")
            if failures >= 3:
                raise PhaseError(f"notes keep failing ({error}); fix the note model and resume")
        else:
            failures = 0
        model = chat.model
        if not note or not all(label in note for label in LABELS):
            # Shown this time, but not cached: the next run writes a proper note.
            note = note or f"**What it is.** {row['abstract'] or row['title']}"
            model = ""
            job.log(f"[warn] note for {pick.id} is not in the usual shape")
        job.store.update_item(pick.id, note=note, note_model=model)
        job.store.commit()
        job.log(f"  note {n}/{len(picks)}: {row['title'][:70]}")
        job.event("read", done=n, total=len(picks))


def write_mentions(job: Job, picks: list[Any]) -> None:
    if not picks:
        return
    chat = job.chat("mention")
    rows = {r["id"]: r for r in job.store.items([p.id for p in picks])}
    todo = [p.id for p in picks if not rows[p.id]["oneliner"]]
    system = prompts.render("mention")
    for batch in batches(todo, 10):
        sids = short_ids(batch)
        lines = [f"{sid} {rows[i]['title']}\n    {' '.join((rows[i]['abstract'] or '').split())[:700]}"
                 for sid, i in sids.items()]
        try:
            reply = chat.complete(system, "Items:\n\n" + "\n".join(lines),
                                  max_tokens=70 * len(batch) + 100)
        except Exception as exc:  # noqa: BLE001
            job.log(f"[warn] mentions batch failed: {exc}")
            continue
        for sid, sentence in parse_lines(reply).items():
            if sid in sids and sentence:
                job.store.update_item(sids[sid], oneliner=_drop_title(
                    sentence.lstrip(":- ").strip(), rows[sids[sid]]["title"] or ""))
        job.store.commit()
    job.log(f"  wrote {len(todo)} one-line mentions")


def _drop_title(sentence: str, title: str) -> str:
    """'Title: This paper does X' -> 'Does X'. Small models echo the title often."""
    if title and sentence.lower().startswith(title.lower()):
        rest = sentence[len(title):].lstrip(" :—-–")
        rest = re.sub(r"^(this (paper|work|study|repo(sitory)?)\s+)", "", rest, flags=re.I)
        if len(rest) > 20:
            return rest[0].upper() + rest[1:]
    return sentence


def signals_for(row: Any) -> dict[str, Any]:
    return json.loads(row["signals"] or "{}")
