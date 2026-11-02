"""Directions: one Markdown file per research direction.

The file name is the direction's stable id and its text is the description; no
other structure is expected. Prefix a file with `_` to pause it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_NON_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Direction:
    id: str
    description: str

    @property
    def fingerprint(self) -> str:
        """Changes when the description's words change, not its line wrapping."""
        words = " ".join(self.description.split())
        return hashlib.sha256(words.encode()).hexdigest()[:16]


def load_directions(folder: Path) -> list[Direction]:
    directions = []
    for path in sorted(folder.glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        if text and not path.name.startswith(("_", ".")):
            directions.append(Direction(path.stem, text))
    return directions


def slugify(text: str, *, max_length: int = 50) -> str:
    slug = _NON_SLUG.sub("-", text.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "direction"
