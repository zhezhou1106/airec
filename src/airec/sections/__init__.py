"""The section registry. Importing any module in this package registers its sections."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from pydantic import BaseModel

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


def validate_block(key: str, block: dict[str, Any] | None) -> tuple[bool, BaseModel]:
    """A `sections.<key>` block from config.yaml as (enabled, the section's Config).

    Raises KeyError for an unknown section and pydantic's ValidationError for bad options.
    """
    options: dict[str, Any] = dict(block or {})
    enabled = bool(options.pop("enabled", True))
    return enabled, available()[key].Config.model_validate(options)


def build_sections(ctx: Context) -> list[Section]:
    """The enabled sections, in the order config.yaml lists them."""
    registry = available()
    built = []
    for key, block in ctx.settings.app.sections.items():
        if key not in registry:
            raise ConfigError(f"Unknown section '{key}'. Available: {', '.join(sorted(registry))}.")
        try:
            enabled, config = validate_block(key, block)
        except ValueError as exc:
            raise ConfigError(f"sections.{key} in config.yaml is invalid:\n{exc}") from exc
        if enabled:
            built.append(registry[key](ctx, config))
    return built
