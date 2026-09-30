"""Read and write keys in .env without disturbing other lines or comments."""
from __future__ import annotations

import os
import re
from pathlib import Path

KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def mask(value: str) -> str:
    if not value:
        return ""
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def set_key(path: Path, key: str, value: str) -> None:
    if not KEY.match(key):
        raise ValueError(f"not a valid key name: {key}")
    if "\n" in value or "\r" in value:
        raise ValueError("a key cannot contain line breaks")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done = False
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if m and m.group(1) == key:
            lines[i] = f"{key}={value}"
            done = True
    if not done:
        lines.append(f"{key}={value}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
