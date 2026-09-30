"""Will a model run on your machine? Arithmetic, not a model call.

Only model releases get this check, and only when the size is known. The
device is described in settings.yaml (device.name, device.memory_gb); a
Mac's unified memory is shared with the system, so ~75% is counted as usable.
"""
from __future__ import annotations

from typing import Any

# bytes per parameter, and a flat allowance for runtime / KV cache
PRECISIONS = (("full precision", 2.0), ("8-bit", 1.07), ("4-bit", 0.6))
OVERHEAD_GB = 2.0


def fit(params: int | float | None, settings: dict[str, Any]) -> str:
    """A short tag like 'runs on your MacBook (4-bit)' or 'too large for your MacBook'."""
    device = settings.get("device") or {}
    memory = float(device.get("memory_gb") or 0)
    if not params or memory <= 0:
        return ""
    usable = memory * 0.75
    name = device.get("name") or "your machine"
    for label, bytes_per in PRECISIONS:
        need = params * bytes_per / 1e9 + OVERHEAD_GB
        if need <= usable:
            return f"fits {name} ({label}, ~{need:.0f} GB)"
    return f"too large for {name} (~{params * 0.6 / 1e9 + OVERHEAD_GB:.0f} GB at 4-bit)"


def size_label(params: int | float | None) -> str:
    if not params:
        return ""
    return f"{params / 1e9:.1f}B" if params >= 1e9 else f"{params / 1e6:.0f}M"
