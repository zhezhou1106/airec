"""An overview image for each report, drawn by Codex once the text is done.

airec does not call an image API itself. It runs `codex exec` (the Codex CLI,
signed in with ChatGPT, so no API key) in a scratch folder holding the finished
report, and asks it to read the report, design one overview picture and save it
there. Codex's image tool also keeps a copy under ~/.codex/generated_images/<thread>,
which is the fallback if the agent forgets the final copy.

A cover is decoration: any failure here is logged and the report stays as it is.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from airec.config import TOOL_PATH, CoverConfig

log = logging.getLogger(__name__)

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_IMAGE_NAME = "overview.png"


class CoverError(RuntimeError):
    pass


def build_prompt(title: str, week: str, style: str) -> str:
    return f"""You are making the overview image for this week's issue of "{title}" ({week}).

1. Read ./report.md in full. It is the finished report: rising GitHub repos, AI news and new papers.
2. Pick the 3 to 5 developments that best sum up the week, and the thread that connects them.
3. Use your image generation tool to create ONE landscape image, 16:9, that gives a visual
   overview of the week: one clear composition in which each picked development has its own
   recognisable motif (no logos or real people). Text in the image: only the title
   "{title} · {week}" plus at most one short label (2-4 words) per motif; spell every word exactly.
   Style: {style}
4. Save the generated image in the current directory as {_IMAGE_NAME} (copy it from wherever
   the tool saved it). Do not modify report.md or create any other file.
5. Reply with one line: the developments you depicted, separated by "; ".
"""


def generate(
    markdown: str,
    title: str,
    week: str,
    config: CoverConfig,
    target: Path,
    events_log: Path | None = None,
) -> str:
    """Draw the cover for `markdown` into `target`. Returns Codex's one-line summary of it."""
    codex = shutil.which(config.command, path=TOOL_PATH)
    if not codex:
        raise CoverError(f"'{config.command}' is not installed (brew install codex).")
    with tempfile.TemporaryDirectory(prefix="airec-cover-") as folder:
        work = Path(folder)
        (work / "report.md").write_text(markdown, encoding="utf-8")
        last = work / ".last-message.txt"
        command = [
            codex,
            "exec",
            "--skip-git-repo-check",
            "-s",
            "workspace-write",
            "-C",
            str(work),
            "--json",
            "-o",
            str(last),
        ]
        if config.model:
            command += ["-m", config.model]
        if config.reasoning:
            command += ["-c", f'model_reasoning_effort="{config.reasoning}"']
        command.append("-")
        log.info("cover: asking Codex for %s", week)
        try:
            done = subprocess.run(
                command,
                input=build_prompt(title, week, config.style),
                text=True,
                capture_output=True,
                timeout=config.timeout,
                cwd=work,
            )
        except subprocess.TimeoutExpired:
            raise CoverError(f"Codex took longer than {config.timeout}s.") from None
        events = _events(done.stdout)
        if events_log:
            events_log.parent.mkdir(parents=True, exist_ok=True)
            events_log.write_text(done.stdout, encoding="utf-8")
        failure = _failure(events)
        if done.returncode != 0 or failure:
            raise CoverError(
                failure or f"codex exited {done.returncode}: {done.stderr.strip()[-300:]}"
            )
        image = _find_image(work, events)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image, target)
        return last.read_text(encoding="utf-8").strip() if last.exists() else ""


def _events(stdout: str) -> list[dict]:
    out = []
    for line in stdout.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _failure(events: list[dict]) -> str | None:
    for event in events:
        if event.get("type") == "turn.failed":
            message = (event.get("error") or {}).get("message") or "turn failed"
            with contextlib.suppress(ValueError, KeyError, TypeError):  # often a JSON error body
                message = json.loads(message)["error"]["message"]
            return f"Codex: {message}"[:300]
    return None


def _find_image(work: Path, events: list[dict]) -> Path:
    candidates = [work / _IMAGE_NAME]
    thread = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    if thread:
        codex_home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        made = (codex_home / "generated_images" / thread).glob("*")
        candidates += sorted(made, key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        if path.is_file() and _is_image(path):
            return path
    raise CoverError("Codex finished without producing an image.")


def _is_image(path: Path) -> bool:
    with path.open("rb") as handle:
        head = handle.read(8)
    return path.stat().st_size > 10_000 and (head.startswith(_PNG) or head.startswith(_JPEG))
