"""Resource path lookup, transparent to PyInstaller `--onefile` bundles."""
from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """Return the base directory where bundled resources live.

    - When frozen by PyInstaller `--onefile`, this is `sys._MEIPASS`.
    - When run from source, this is the repository root.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)
