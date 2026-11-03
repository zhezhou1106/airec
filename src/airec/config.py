"""Where airec keeps things, and the validated shape of its configuration.

Code lives in the repo; everything personal lives in one home directory
(`~/.airec`, or `$AIREC_HOME`): config, keys, directions, database and reports.

The `sections:` block of config.yaml is deliberately untyped here. Each section
validates its own block with its own `Config` model, so adding a section never
touches this file.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULTS = Path(__file__).parent / "defaults"


class ConfigError(RuntimeError):
    """The user's configuration is missing or invalid."""


class Strict(BaseModel):
    """A config block in which a mistyped key is an error, not a silent no-op."""

    model_config = ConfigDict(extra="forbid")


class Provider(Strict):
    base_url: str
    api_key_env: str | None = None
    timeout: float = 240


class Role(Strict):
    provider: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 16000  # reasoning models spend much of this first


class LlmConfig(Strict):
    providers: dict[str, Provider]
    roles: dict[str, Role]


class ReportConfig(Strict):
    title: str = "AI Weekly"
    reader: str = ""  # who the report is for; tailors the news and repo sections
    first_window_days: int = Field(default=7, ge=1)
    max_window_days: int = Field(default=14, ge=1)  # the catch-up limit after missed weeks
    copy_to: Path | None = None  # e.g. an Obsidian folder
    notify: bool = True
    link: str | None = None  # what a notification opens; {path} is relative to the home


class CoverConfig(Strict):
    """An overview image drawn by the Codex CLI after the text is written (see cover.py)."""

    enabled: bool = False
    command: str = "codex"
    model: str | None = None  # None uses Codex's own default from ~/.codex/config.toml
    reasoning: str | None = "medium"
    timeout: int = Field(default=900, ge=60)  # seconds
    style: str = (
        "a clean editorial illustration, flat shapes with soft depth, deep navy background "
        "with coral and sky-blue accents, generous negative space, readable at thumbnail size"
    )


class ScheduleConfig(Strict):
    weekday: int = Field(default=1, ge=1, le=7)  # ISO weekday: 1 is Monday
    hour: int = Field(default=9, ge=0, le=23)  # local time
    max_attempts: int = Field(default=3, ge=1)  # per week, when sections keep failing


class AppConfig(Strict):
    llm: LlmConfig
    report: ReportConfig = ReportConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    cover: CoverConfig = CoverConfig()
    sections: dict[str, dict[str, Any] | None]  # report order; validated by each section


@dataclass(frozen=True)
class Paths:
    home: Path

    @classmethod
    def resolve(cls) -> Paths:
        return cls(Path(os.environ.get("AIREC_HOME") or "~/.airec").expanduser())

    @property
    def config(self) -> Path:
        return self.home / "config.yaml"

    @property
    def env(self) -> Path:
        return self.home / ".env"

    @property
    def directions(self) -> Path:
        return self.home / "directions"

    @property
    def db(self) -> Path:
        return self.home / "airec.sqlite"

    @property
    def reports(self) -> Path:
        return self.home / "reports"

    @property
    def drafts(self) -> Path:
        return self.reports / "drafts"

    @property
    def assets(self) -> Path:
        return self.reports / "assets"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def home_card(self) -> Path:
        return self.home / "hub-card.json"

    @property
    def lock(self) -> Path:
        return self.home / "run.lock"


@dataclass(frozen=True)
class Secrets:
    """API keys: the process environment wins over the home `.env`."""

    file_values: dict[str, str | None]

    @classmethod
    def load(cls, path: Path) -> Secrets:
        return cls(dict(dotenv_values(path)) if path.exists() else {})

    def get(self, name: str | None) -> str:
        if not name:
            return ""
        return os.environ.get(name) or self.file_values.get(name) or ""


@dataclass(frozen=True)
class Settings:
    paths: Paths
    app: AppConfig

    @cached_property
    def secrets(self) -> Secrets:
        return Secrets.load(self.paths.env)


def load_settings(paths: Paths | None = None) -> Settings:
    paths = paths or Paths.resolve()
    if not paths.config.exists():
        raise ConfigError(f"No config at {paths.config}. Run `rec init` first.")
    try:
        raw = yaml.safe_load(paths.config.read_text(encoding="utf-8")) or {}
        app = AppConfig.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"{paths.config} is invalid:\n{exc}") from exc
    for name, role in app.llm.roles.items():
        if role.provider not in app.llm.providers:
            raise ConfigError(f"LLM role '{name}' uses unknown provider '{role.provider}'.")
    return Settings(paths, app)


def init_home(paths: Paths) -> list[Path]:
    """Create the home directory from the packaged defaults; never overwrite."""
    for directory in (paths.home, paths.directions, paths.reports, paths.logs):
        directory.mkdir(parents=True, exist_ok=True)
    created = []
    for source, target in (
        (DEFAULTS / "config.yaml", paths.config),
        (DEFAULTS / "env.example", paths.env),
    ):
        if not target.exists():
            shutil.copyfile(source, target)
            created.append(target)
    paths.env.chmod(0o600)
    if not any(paths.directions.glob("*.md")):
        for source in sorted((DEFAULTS / "directions").glob("*.md")):
            target = paths.directions / source.name
            shutil.copyfile(source, target)
            created.append(target)
    return created
