"""Persisted user settings stored in a per-user JSON file."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _settings_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "ImageConverter"


def settings_path() -> Path:
    return _settings_dir() / "settings.json"


def load() -> dict[str, Any]:
    p = settings_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(data: dict[str, Any]) -> None:
    try:
        d = _settings_dir()
        d.mkdir(parents=True, exist_ok=True)
        settings_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        # Settings persistence is best-effort; never crash on it.
        pass
