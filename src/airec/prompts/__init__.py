"""Prompt text lives in the .md files next to this one: edit wording there.

Placeholders are written {{name}} and filled by render().
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).parent
_VAR = re.compile(r"\{\{(\w+)\}\}")


def render(name: str, **values: object) -> str:
    text = (HERE / f"{name}.md").read_text(encoding="utf-8")

    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"prompt {name} needs {{{{{key}}}}}")
        return str(values[key])

    return _VAR.sub(sub, text).strip()
