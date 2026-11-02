"""Turning a description into a search plan, once per version of the description."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from airec import prompts
from airec.sections.base import Context
from airec.sections.papers.directions import Direction

_CATEGORY = re.compile(r"^[a-z-]+(\.[A-Za-z-]+)?$")


class SearchPlan(BaseModel):
    title: str
    phrases: list[str] = Field(min_length=3)
    categories: list[str] = Field(default_factory=list)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @field_validator("phrases")
    @classmethod
    def _clean_phrases(cls, phrases: list[str]) -> list[str]:
        cleaned = [" ".join(phrase.replace('"', "").split()) for phrase in phrases]
        return list(dict.fromkeys(phrase for phrase in cleaned if phrase))

    @field_validator("categories")
    @classmethod
    def _valid_categories(cls, categories: list[str]) -> list[str]:
        return [category for category in categories if _CATEGORY.match(category)]

    def matches(self, title: str, abstract: str) -> bool:
        """True when any phrase occurs in the text, its words in any order."""
        text = f"{title} {abstract}".lower()
        return any(all(word in text for word in phrase.lower().split()) for phrase in self.phrases)

    def criteria_text(self) -> str:
        lines = [f"Counts: {item}" for item in self.include]
        lines += [f"Does not count: {item}" for item in self.exclude]
        return "\n".join(lines) or "(none beyond the description)"


def compile_plan(ctx: Context, description: str) -> SearchPlan:
    system = prompts.render("paper_plan")
    return ctx.llm.json("writer", system, description, SearchPlan)


def load_or_compile_plan(ctx: Context, section: str, direction: Direction) -> SearchPlan:
    key = f"plan:{direction.id}"
    stored = ctx.store.get_state(section, key)
    if stored and stored.get("fingerprint") == direction.fingerprint:
        return SearchPlan.model_validate(stored["plan"])
    plan = compile_plan(ctx, direction.description)
    ctx.store.set_state(
        section, key, {"fingerprint": direction.fingerprint, "plan": plan.model_dump()}
    )
    return plan
