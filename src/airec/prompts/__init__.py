"""Prompt text lives in the .md files next to this one; edit wording there.

Placeholders are written {{name}}. A missing value is an error, so a renamed
variable cannot silently reach the model as literal braces.
"""

from __future__ import annotations

import re
from pathlib import Path

_HERE = Path(__file__).parent
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render(name: str, **values: object) -> str:
    template = (_HERE / f"{name}.md").read_text(encoding="utf-8").strip()

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"prompt '{name}' needs {{{{{key}}}}}")
        return str(values[key])

    return _PLACEHOLDER.sub(fill, template)
