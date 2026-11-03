"""Talking to my-hub, when the app runs as one of its services.

HUB_API and HUB_TOKEN (set by the hub for services) let the server read the hub's
appearance for the first paint and open links through the hub.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

_DARK_STYLES = {"terminal", "mocha", "macchiato", "frappe"}


def api_url() -> str:
    """The hub's API, or empty when the app runs on its own."""
    return os.environ.get("HUB_API", "").rstrip("/")


def request(method: str, path: str, **kwargs: Any) -> Any:
    """Call the hub's API with this service's token; raises httpx.HTTPError."""
    token = os.environ.get("HUB_TOKEN", "")
    reply = httpx.request(
        method, f"{api_url()}{path}", headers={"X-Hub-Token": token}, timeout=10, **kwargs
    )
    reply.raise_for_status()
    return reply.json()


def appearance() -> dict[str, Any]:
    """The hub's current style; live changes arrive in the page by postMessage."""
    fallback = {"style": "terminal", "theme": "dark", "term": True}
    if not api_url():
        return fallback
    try:
        look = request("GET", "/api/settings")["settings"]["appearance"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return fallback
    style = str(look.get("style") or "terminal")
    if not re.fullmatch(r"[a-z]+", style):
        return fallback
    if style == "latte":
        theme = "light"
    elif style in _DARK_STYLES:
        theme = "dark"
    else:
        theme = "dark" if look.get("theme") == "dark" else "light"
    return {"style": style, "theme": theme, "term": style != "classic"}
