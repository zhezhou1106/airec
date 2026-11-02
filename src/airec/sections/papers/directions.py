"""Directions: one Markdown file per research direction.

The file name is the direction's stable id and its text is the description; no
other structure is expected. Prefix a file with `_` to pause it.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_VALID_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REMOVED_FOLDER = ".removed"


@dataclass(frozen=True)
class Direction:
    id: str
    description: str
    paused: bool = False

    @property
    def fingerprint(self) -> str:
        """Changes when the description's words change, not its line wrapping."""
        words = " ".join(self.description.split())
        return hashlib.sha256(words.encode()).hexdigest()[:16]


def load_directions(folder: Path, *, include_paused: bool = False) -> list[Direction]:
    directions = []
    for path in sorted(folder.glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        paused = path.name.startswith("_")
        if not text or path.name.startswith(".") or (paused and not include_paused):
            continue
        directions.append(Direction(path.stem.removeprefix("_"), text, paused))
    return directions


def slugify(text: str, *, max_length: int = 50) -> str:
    slug = _NON_SLUG.sub("-", text.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "direction"


def is_valid_id(direction_id: str) -> bool:
    return bool(_VALID_ID.match(direction_id))


def _existing(folder: Path, direction_id: str) -> Path | None:
    for name in (f"{direction_id}.md", f"_{direction_id}.md"):
        if (folder / name).exists():
            return folder / name
    return None


def save_direction(folder: Path, direction_id: str, description: str) -> None:
    """Create a direction or replace its text, keeping it paused if it was."""
    folder.mkdir(parents=True, exist_ok=True)
    path = _existing(folder, direction_id) or folder / f"{direction_id}.md"
    path.write_text(description.strip() + "\n", encoding="utf-8")


def unique_id(folder: Path, name: str) -> str:
    base = slugify(name)
    candidate, number = base, 2
    while _existing(folder, candidate):
        candidate, number = f"{base}-{number}", number + 1
    return candidate


def set_paused(folder: Path, direction_id: str, paused: bool) -> None:
    path = _existing(folder, direction_id)
    if path is None:
        raise FileNotFoundError(direction_id)
    path.rename(folder / f"{'_' if paused else ''}{direction_id}.md")


def remove_direction(folder: Path, direction_id: str) -> None:
    """Move a direction aside rather than delete it: its description is the user's writing."""
    path = _existing(folder, direction_id)
    if path is None:
        raise FileNotFoundError(direction_id)
    removed = folder / REMOVED_FOLDER
    removed.mkdir(exist_ok=True)
    shutil.move(str(path), removed / path.name)
