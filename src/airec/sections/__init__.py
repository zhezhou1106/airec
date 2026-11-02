"""The section registry. Importing any module in this package registers its sections."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from airec.config import ConfigError
from airec.sections.base import Context, Section

_REGISTRY: dict[str, type[Section]] = {}


def register(cls: type[Section]) -> type[Section]:
    _REGISTRY[cls.key] = cls
    return cls


def available() -> dict[str, type[Section]]:
    for module in pkgutil.iter_modules(__path__):
        if module.name != "base":
            importlib.import_module(f"{__name__}.{module.name}")
    return dict(_REGISTRY)


def build_sections(ctx: Context) -> list[Section]:
    """The enabled sections, in the order config.yaml lists them."""
    registry = available()
    built = []
    for key, block in ctx.settings.app.sections.items():
        if key not in registry:
            raise ConfigError(f"Unknown section '{key}'. Available: {', '.join(sorted(registry))}.")
        options: dict[str, Any] = dict(block or {})
        if not options.pop("enabled", True):
            continue
        cls = registry[key]
        try:
            config = cls.Config.model_validate(options)
        except ValueError as exc:
            raise ConfigError(f"sections.{key} in config.yaml is invalid:\n{exc}") from exc
        built.append(cls(ctx, config))
    return built
