"""Configuration: four YAML files in config/ and secrets in .env.

config/ and .env are yours and never committed; missing files are created from
config.example/ and .env.example on first use (or with `rec init`).

    config/interests.yaml   the interest map (what you care about)
    config/sources.yaml     where to look
    config/models.yaml      which model runs each phase
    config/settings.yaml    digest size, news, output, device
    .env                    API keys only

Every path the app stores is relative to the project root, so the folder can
be moved without breaking old runs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

USER_AGENT = "airec/0.2 (personal research digest)"

CONFIG_FILES = ("interests", "sources", "models", "settings")

DEFAULT_SETTINGS: dict[str, Any] = {
    "digest": {
        "notes": 15,
        "mentions": 30,
        "min_score_note": 7.0,
        "min_score_mention": 6.0,
        "max_notes_per_topic": 4,
        "explore_share": 0.2,
    },
    "news": {"enabled": False, "max_days": 7, "stories": 10},
    "run": {
        "first_run_days": 7,
        "triage_batch": 25,
        "score_batch": 8,
        "triage_max": 3000,
        "resurface": 40,
        "read_chars": 14000,
    },
    "output": {"obsidian_dir": ""},
    "index": {"backfill_months": 12},
    "device": {"name": "", "memory_gb": 0},
    "search": {"order": ["tavily", "brave", "duckduckgo"]},
}

DEFAULT_MODELS: dict[str, Any] = {
    "providers": {},
    "embedding": {
        "base_url": "http://127.0.0.1:8081/v1",
        "model": "qwen3-embedding-0.6b",
        "api_key_env": "",
        "autostart": True,
        "batch": 32,
        "timeout": 120,
    },
    "phases": {},
}

PHASES = ("triage", "score", "note", "mention", "article", "news", "deep_read", "map_editor")


class ConfigError(ValueError):
    pass


@dataclass
class Endpoint:
    """One model as a phase uses it."""
    phase: str
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: float = 300
    local: bool = False       # send "thinking off" hints that local Qwen servers honor
    json_mode: bool = False


@dataclass
class EmbedEndpoint:
    base_url: str
    api_key: str
    model: str
    autostart: bool
    batch: int
    timeout: float


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def project_root() -> Path:
    return Path(os.environ.get("AIREC_HOME") or Path.cwd()).resolve()


class Config:
    def __init__(self, root: Path | None = None):
        self.root = (root or project_root()).resolve()
        self.config_dir = Path(os.environ.get("AIREC_CONFIG_DIR") or self.root / "config")
        self.data_dir = Path(os.environ.get("AIREC_DATA_DIR") or self.root / "data")
        self.env_path = Path(os.environ.get("AIREC_ENV_FILE") or self.root / ".env")
        self.init_config()
        self.reload()

    def init_config(self) -> list[str]:
        """Copy any missing config file from config.example/ (your config/ is never committed)."""
        example = self.root / "config.example"
        created = []
        if not example.is_dir():
            return created
        for name in CONFIG_FILES:
            target, source = self.path(name), example / f"{name}.yaml"
            if not target.exists() and source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                created.append(target.name)
        env_example = self.root / ".env.example"
        if not self.env_path.exists() and env_example.exists():
            self.env_path.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
            os.chmod(self.env_path, 0o600)
            created.append(".env")
        return created

    # ---------- loading ----------
    def reload(self) -> None:
        self.raw = {name: self._read_yaml(name) for name in CONFIG_FILES}
        self.interests: dict[str, Any] = self.raw["interests"] or {}
        self.sources: dict[str, Any] = self.raw["sources"] or {}
        self.models = deep_merge(DEFAULT_MODELS, self.raw["models"] or {})
        self.settings = deep_merge(DEFAULT_SETTINGS, self.raw["settings"] or {})
        self.env = {k: v or "" for k, v in dotenv_values(self.env_path).items()} \
            if self.env_path.exists() else {}

    def _read_yaml(self, name: str) -> dict[str, Any]:
        path = self.path(name)
        if not path.exists():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path.name} must be a mapping at the top level")
        return data

    def path(self, name: str) -> Path:
        return self.config_dir / f"{name}.yaml"

    # ---------- secrets ----------
    def secret(self, key: str) -> str:
        if not key:
            return ""
        return os.environ.get(key) or self.env.get(key, "")

    # ---------- derived paths ----------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "airec.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def digests_dir(self) -> Path:
        return self.data_dir / "digests"

    @property
    def history_dir(self) -> Path:
        return self.data_dir / "history"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "run.lock"

    def rel(self, path: Path) -> str:
        """A path as stored: relative to the project root when inside it."""
        path = Path(path).resolve()
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def abs(self, stored: str) -> Path:
        p = Path(stored)
        return p if p.is_absolute() else self.root / p

    # ---------- models ----------
    def endpoint(self, phase: str) -> Endpoint:
        phases = self.models.get("phases") or {}
        conf = phases.get(phase)
        if not conf:
            raise ConfigError(f"models.yaml has no model for the '{phase}' phase")
        provider_name = conf.get("provider", "")
        provider = (self.models.get("providers") or {}).get(provider_name)
        if not provider:
            raise ConfigError(f"phase '{phase}' uses provider '{provider_name}', "
                              "which models.yaml does not define")
        if not conf.get("model"):
            raise ConfigError(f"phase '{phase}' has no model name")
        return Endpoint(
            phase=phase,
            provider=provider_name,
            base_url=str(provider.get("base_url", "")).rstrip("/"),
            api_key=self.secret(provider.get("api_key_env", "")),
            model=str(conf["model"]),
            temperature=float(conf.get("temperature", provider.get("temperature", 0.2))),
            max_tokens=int(conf.get("max_tokens", provider.get("max_tokens", 4096))),
            timeout=float(conf.get("timeout", provider.get("timeout", 300))),
            local=bool(provider.get("local", False)),
            json_mode=bool(provider.get("json_mode", False)),
        )

    def missing_key(self, phase: str) -> str:
        """The env var a phase needs but does not have, or ''."""
        conf = (self.models.get("phases") or {}).get(phase) or {}
        provider = (self.models.get("providers") or {}).get(conf.get("provider", "")) or {}
        key = provider.get("api_key_env", "")
        return key if key and not self.secret(key) else ""

    def embedding(self) -> EmbedEndpoint:
        e = self.models["embedding"]
        return EmbedEndpoint(
            base_url=str(e["base_url"]).rstrip("/"),
            api_key=self.secret(e.get("api_key_env", "")),
            model=str(e["model"]),
            autostart=bool(e.get("autostart", True)),
            batch=int(e.get("batch", 32)),
            timeout=float(e.get("timeout", 120)),
        )

    # ---------- interest map ----------
    def topics(self) -> list[dict[str, Any]]:
        return [t for t in (self.interests.get("topics") or []) if isinstance(t, dict)]

    def map_hash(self) -> str:
        """Changes whenever the part of the map that affects judging changes."""
        keep = {
            "about": self.interests.get("about", ""),
            "topics": [
                {k: t.get(k) for k in ("id", "priority", "summary", "include", "exclude")}
                for t in self.topics()
            ],
            "exclude_everywhere": self.interests.get("exclude_everywhere") or [],
        }
        blob = json.dumps(keep, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]

    # ---------- shortcuts ----------
    def s(self, section: str, key: str) -> Any:
        return self.settings.get(section, {}).get(key, DEFAULT_SETTINGS[section].get(key))


def load_config(root: Path | None = None) -> Config:
    return Config(root)
