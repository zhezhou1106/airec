"""The contract for a report section, and what a section can reach.

To add a section, create a module in this package:

    @register
    class Podcasts(Section):
        key = "podcasts"
        title = "Podcasts"

        class Config(Strict):
            feeds: list[str] = []

        def run(self, window: Window) -> SectionResult:
            ...

then add a `podcasts:` block to config.yaml. Its position there is its position
in the report. Nothing else changes: the pipeline gives it a time window, keeps
one failure from affecting the other sections, and records what it showed.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel

from airec.config import Secrets, Settings, Strict
from airec.http import Http
from airec.llm import Llm
from airec.sources.github import GitHub
from airec.store import Store

T = TypeVar("T")
log = logging.getLogger(__name__)
WORKERS = 6  # concurrent requests one section makes to its sources


class SectionError(RuntimeError):
    """The section produced nothing usable, so its window should be retried."""


@dataclass(frozen=True)
class Window:
    """The time a section covers, in UTC."""

    since: datetime
    until: datetime

    def contains(self, moment: datetime) -> bool:
        return self.since <= moment <= self.until

    @property
    def days(self) -> float:
        return (self.until - self.since).total_seconds() / 86400

    @property
    def label(self) -> str:
        return f"{self.since:%b %-d} to {self.until:%b %-d, %Y}"


@dataclass
class SectionResult:
    markdown: str
    highlights: list[str] = field(default_factory=list)  # bullets for "At a glance"
    shown: list[str] = field(default_factory=list)  # ids not to repeat next week
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    """Shared services for one run."""

    settings: Settings
    store: Store
    http: Http
    llm: Llm
    week: str

    @property
    def secrets(self) -> Secrets:
        return self.settings.secrets

    @cached_property
    def github(self) -> GitHub:
        """One GitHub client per run, authenticated when GITHUB_TOKEN is set."""
        return GitHub(self.http, self.secrets.get("GITHUB_TOKEN"))

    @property
    def reader(self) -> str:
        return self.settings.app.report.reader.strip() or "An AI practitioner."


class Section(ABC):
    key: ClassVar[str]
    title: ClassVar[str]
    Config: ClassVar[type[BaseModel]] = Strict
    schema: ClassVar[str] = ""  # idempotent SQL for tables this section owns

    def __init__(self, ctx: Context, config: Any):
        self.ctx = ctx
        self.config = config
        self.warnings: list[str] = []
        self._failed_sources = 0
        self._ok_sources = 0
        self._lock = threading.Lock()  # sources may be gathered from worker threads
        ctx.store.ensure_schema(self.schema)

    @abstractmethod
    def run(self, window: Window) -> SectionResult:
        """Produce this section for `window`. Raise SectionError to report a failure."""

    def checks(self) -> list[tuple[str, Callable[[], str]]]:
        """Named health probes for `rec doctor`. Each returns a detail or raises."""
        return []

    def warn(self, message: str) -> None:
        log.warning("%s: %s", self.key, message)
        with self._lock:
            self.warnings.append(message)

    def gather(self, source: str, fetch: Callable[[], T], default: T) -> T:
        """Call one data source; a failing source costs its items, not the section."""
        try:
            value = fetch()
        except Exception as exc:
            with self._lock:
                self._failed_sources += 1
            self.warn(f"{source} failed: {type(exc).__name__}: {str(exc)[:160]}")
            return default
        with self._lock:
            self._ok_sources += 1
        return value

    def gather_all(
        self, jobs: Sequence[tuple[str, Callable[[], T]]], default: T, workers: int = WORKERS
    ) -> list[T]:
        """`gather` each (source, fetch) job, a few at a time; results keep the jobs' order."""
        if not jobs:
            return []
        pool = ThreadPoolExecutor(min(workers, len(jobs)))
        try:
            return list(pool.map(lambda job: self.gather(job[0], job[1], default), jobs))
        finally:
            pool.shutdown(cancel_futures=True)  # on Ctrl-C, start nothing more

    def require_sources(self) -> None:
        """Fail the section when no source answered: an empty report would be a lie."""
        if self._failed_sources and not self._ok_sources:
            raise SectionError("every data source failed")
