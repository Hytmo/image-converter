"""Top-level entry point for the Image Converter app."""
import os
import sys
from pathlib import Path


def _ensure_tcl_paths() -> None:
    """Ensure TCL_LIBRARY/TK_LIBRARY are set so tkinter can initialize.

    When running from a venv on Windows, the bundled Python sometimes cannot
    locate the Tcl/Tk script libraries on its own. Falling back to the base
    interpreter's `tcl` folder fixes this without affecting installs that
    already work. No effect when running from a PyInstaller bundle.
    """
    if getattr(sys, "frozen", False):
        return
    if os.environ.get("TCL_LIBRARY") and os.environ.get("TK_LIBRARY"):
        return
    base = Path(getattr(sys, "base_prefix", sys.prefix))
    candidates = [base / "tcl", base / "lib", base]
    for root in candidates:
        if not root.is_dir():
            continue
        tcl_dirs = sorted(root.glob("tcl[0-9]*"))
        tk_dirs = sorted(root.glob("tk[0-9]*"))
        tcl = next((d for d in tcl_dirs if (d / "init.tcl").exists()), None)
        tk = next((d for d in tk_dirs if (d / "tk.tcl").exists()), None)
        if tcl:
            os.environ.setdefault("TCL_LIBRARY", str(tcl))
        if tk:
            os.environ.setdefault("TK_LIBRARY", str(tk))
        if tcl and tk:
            return


_ensure_tcl_paths()

from image_converter.app import run  # noqa: E402


if __name__ == "__main__":
    run()
