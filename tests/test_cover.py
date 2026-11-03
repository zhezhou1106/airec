"""The Codex-drawn cover, against a fake `codex` executable."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from airec import cover, report
from airec.config import CoverConfig, ReportConfig

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20_000

REPORT = """---
week: 2026-W45
tags: [airec, weekly]
---

# AI Weekly: 2026-W45

*Oct 26 to Nov 2, 2026*

## At a glance

- Something happened
"""


def fake_codex(tmp_path: Path, body: str) -> CoverConfig:
    script = tmp_path / "fake-codex"
    script.write_text("#!/bin/sh\ncat > /dev/null\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "image.png").write_bytes(PNG)
    return CoverConfig(enabled=True, command=str(script), timeout=60)


def test_image_saved_by_codex_lands_at_the_target(tmp_path):
    config = fake_codex(
        tmp_path,
        f"""
cp {tmp_path}/image.png overview.png
echo '{{"type":"thread.started","thread_id":"t1"}}'
echo '{{"type":"turn.completed"}}'
while [ "$1" != "-o" ]; do shift; done; echo "agents; papers" > "$2"
""",
    )
    target = tmp_path / "out" / "2026-W45.png"
    summary = cover.generate(REPORT, "AI Weekly", "2026-W45", config, target)
    assert target.read_bytes() == PNG
    assert summary == "agents; papers"


def test_falls_back_to_codex_generated_images(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    (home / "generated_images" / "t9").mkdir(parents=True)
    (home / "generated_images" / "t9" / "exec-1.png").write_bytes(PNG)
    monkeypatch.setenv("CODEX_HOME", str(home))
    config = fake_codex(tmp_path, """echo '{"type":"thread.started","thread_id":"t9"}'""")
    target = tmp_path / "c.png"
    cover.generate(REPORT, "AI Weekly", "2026-W45", config, target)
    assert target.read_bytes() == PNG


def test_codex_errors_are_reported(tmp_path):
    msg = '{\\"error\\":{\\"message\\":\\"model not supported\\"}}'
    config = fake_codex(
        tmp_path, f"""echo '{{"type":"turn.failed","error":{{"message":"{msg}"}}}}'"""
    )
    with pytest.raises(cover.CoverError, match="model not supported"):
        cover.generate(REPORT, "AI Weekly", "2026-W45", config, tmp_path / "c.png")


def test_no_image_is_an_error(tmp_path):
    config = fake_codex(tmp_path, "exit 0")
    with pytest.raises(cover.CoverError, match="without producing an image"):
        cover.generate(REPORT, "AI Weekly", "2026-W45", config, tmp_path / "c.png")


def test_cover_goes_under_the_date_line_once_and_is_copied(tmp_path):
    path = tmp_path / "reports" / "2026-W45.md"
    path.parent.mkdir()
    path.write_text(REPORT)
    image = tmp_path / "reports" / "assets" / "2026-W45.png"
    image.parent.mkdir()
    image.write_bytes(PNG)
    vault = tmp_path / "vault"
    config = ReportConfig(copy_to=vault)
    report.attach_cover(path, image, "2026-W45", config)
    report.attach_cover(path, image, "2026-W45", config)  # a redraw doesn't add a second one
    text = path.read_text()
    line = "![AI Weekly 2026-W45 overview](assets/2026-W45.png)"
    assert text.count(line) == 1
    assert text.index("*Oct 26") < text.index(line) < text.index("## At a glance")
    assert (vault / "2026-W45.md").read_text() == text
    assert (vault / "assets" / "2026-W45.png").read_bytes() == PNG
