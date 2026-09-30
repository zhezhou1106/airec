"""Deep read: a long, careful reading of one item, on the model set for 'deep_read'."""
from __future__ import annotations

import prompts
from config import Config
from phases.common import KIND_WORD, Job, PhaseError
from phases.read import Reader, library_path
from runlog import RunContext
from store import Store


def run_deep_read(cfg: Config, rc: RunContext, item_id: str) -> str:
    store = Store(cfg.db_path)
    try:
        row = store.item(item_id)
        if row is None:
            raise PhaseError(f"{item_id} is not in the index")
        rc.update_meta(item_id=item_id, title=row["title"])
        job = Job(cfg, store, rc)
        rc.event("read", status="running")
        text = Reader(cfg, rc.log).text(row, store, 60000)
        rc.log(f"  read {len(text)} characters of {row['title'][:80]}")
        rc.event("read", status="done")
        topic = store.one("SELECT topic FROM shown WHERE item_id=? ORDER BY ts DESC LIMIT 1",
                          (item_id,))
        device = cfg.settings.get("device") or {}
        device_line = ""
        if device.get("name") and row["kind"] in ("repo", "model"):
            device_line = (f"\n   The reader's machine: {device['name']}, "
                           f"{device.get('memory_gb', '?')} GB memory.")
        system = prompts.render("deep_read", kind=KIND_WORD.get(row["kind"], "item"),
                                topic=job.topic_line(topic["topic"] if topic else "other"),
                                device=device_line)
        user = (f"Title: {row['title']}\nURL: {row['url']}\nSummary: {row['abstract'] or ''}\n\n"
                f"SOURCE TEXT:\n{text or '(not available; work from the summary)'}")
        rc.event("write", status="running")
        chat = job.chat("deep_read")
        body = chat.complete(system, user, max_tokens=6000)
        path = library_path(cfg, item_id, row["kind"], ".deep")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {row['title']}\n\n<{row['url']}>\n\n"
                        f"*Deep read by {chat.model}*\n\n{body}\n", encoding="utf-8")
        store.update_item(item_id, deep_path=cfg.rel(path))
        store.commit()
        rc.event("write", status="done")
        rc.log(f"  → {path}")
        return str(path)
    finally:
        store.close()
