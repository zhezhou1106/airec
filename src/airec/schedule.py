"""The weekly schedule, as a macOS launchd agent.

The agent does not fire once a week. It wakes hourly and at login and runs
`rec run --if-due`, which does nothing unless this week's report is still owed.
A Mac that is asleep or switched off at the scheduled hour therefore still
produces the report, at its next chance.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from airec.config import Paths

LABEL = "com.airec.weekly"
INTERVAL_SECONDS = 3600


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(paths: Paths, python: str | None = None) -> dict:
    log = str(paths.logs / "launchd.log")
    return {
        "Label": LABEL,
        "ProgramArguments": [python or sys.executable, "-m", "airec", "run", "--if-due"],
        "EnvironmentVariables": {"AIREC_HOME": str(paths.home)},
        "StartInterval": INTERVAL_SECONDS,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(paths: Paths) -> Path:
    paths.logs.mkdir(parents=True, exist_ok=True)
    target = plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["launchctl", "bootout", f"{_domain()}/{LABEL}"], check=False, capture_output=True
    )
    target.write_bytes(plistlib.dumps(build_plist(paths)))
    subprocess.run(["launchctl", "bootstrap", _domain(), str(target)], check=True)
    return target


def uninstall() -> bool:
    target = plist_path()
    if not target.exists():
        return False
    subprocess.run(
        ["launchctl", "bootout", f"{_domain()}/{LABEL}"], check=False, capture_output=True
    )
    target.unlink()
    return True


def is_installed() -> bool:
    result = subprocess.run(
        ["launchctl", "print", f"{_domain()}/{LABEL}"], check=False, capture_output=True
    )
    return result.returncode == 0
