"""customtkinter GUI for the image converter."""
from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

try:
    import customtkinter as ctk
    USING_CTK = True
except ImportError:
    import tkinter as ctk  # type: ignore
    USING_CTK = False

import tkinter as tk
from tkinter import filedialog, messagebox

from .converter import (
    ConvertOptions,
    ConvertResult,
    FORMAT_TO_EXT,
    LOSSY_FORMATS,
    SUPPORTED_INPUT_EXTS,
    convert_one,
    human_size,
    is_supported,
)

try:
    import windnd  # type: ignore
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


APP_TITLE = "Image Converter"
APP_VERSION = "1.0.0"
FORMATS = ["JPG", "PNG", "WEBP", "BMP", "TIFF", "GIF"]


class FileEntry:
    __slots__ = ("path", "size", "format_hint", "frame")

    def __init__(self, path: Path, size: int, fmt: str):
        self.path = path
        self.size = size
        self.format_hint = fmt
        self.frame = None


def _format_hint(path: Path) -> str:
    return path.suffix.lstrip(".").upper() or "?"


class App(ctk.CTk if USING_CTK else tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        if USING_CTK:
            ctk.set_appearance_mode("system")
            ctk.set_default_color_theme("blue")

        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("960x720")
        self.minsize(820, 600)

        self.files: list[FileEntry] = []
        self._worker: Optional[threading.Thread] = None
        self._msg_queue: "queue.Queue[tuple]" = queue.Queue()
        self._cancel_requested = False

        self._build_layout()
        self._poll_queue()

        if DND_AVAILABLE:
            try:
                windnd.hook_dropfiles(self, func=self._on_drop_paths)
            except Exception:
                pass

    # ----- Layout -----
    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = self._frame(self, padx=12, pady=(12, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(2, weight=1)

        self._button(header, "Add files...", self._on_add_files).grid(row=0, column=0, padx=(0, 8))
        self._button(header, "Clear list", self._on_clear_list, kind="secondary").grid(
            row=0, column=1, padx=(0, 8)
        )

        dnd_hint = "Drag and drop images anywhere in the window" if DND_AVAILABLE else \
                   "Drag-and-drop unavailable (install 'windnd' to enable)"
        self._label(header, dnd_hint, muted=True).grid(row=0, column=2, sticky="e")

        # File list area
        list_wrap = self._frame(self, padx=12, pady=6)
        list_wrap.grid(row=1, column=0, sticky="nsew")
        list_wrap.grid_columnconfigure(0, weight=1)
        list_wrap.grid_rowconfigure(0, weight=1)

        if USING_CTK:
            self.list_frame = ctk.CTkScrollableFrame(list_wrap, label_text="Files")
        else:
            self.list_frame = tk.Frame(list_wrap, borderwidth=1, relief="solid")
        self.list_frame.grid(row=0, column=0, sticky="nsew")

        self.empty_label = self._label(
            self.list_frame,
            "No files yet. Click \"Add files...\" or drop images here.",
            muted=True,
        )
        self.empty_label.grid(row=0, column=0, padx=20, pady=20)

        # Options panel
        opts = self._frame(self, padx=12, pady=6)
        opts.grid(row=2, column=0, sticky="ew")
        for c in range(4):
            opts.grid_columnconfigure(c, weight=1, uniform="opt")

        # Row 0: format + quality
        self._label(opts, "Output format").grid(row=0, column=0, sticky="w", padx=4, pady=(6, 0))
        self._label(opts, "Quality").grid(row=0, column=1, columnspan=2, sticky="w", padx=4, pady=(6, 0))

        self.format_var = tk.StringVar(value="JPG")
        if USING_CTK:
            self.format_menu = ctk.CTkOptionMenu(opts, values=FORMATS, variable=self.format_var,
                                                 command=lambda *_: self._on_format_change())
        else:
            self.format_menu = tk.OptionMenu(opts, self.format_var, *FORMATS,
                                             command=lambda *_: self._on_format_change())
        self.format_menu.grid(row=1, column=0, sticky="ew", padx=4)

        self.quality_var = tk.IntVar(value=85)
        if USING_CTK:
            self.quality_slider = ctk.CTkSlider(
                opts, from_=1, to=100, number_of_steps=99, variable=self.quality_var,
                command=lambda *_: self._update_quality_label()
            )
        else:
            self.quality_slider = tk.Scale(
                opts, from_=1, to=100, orient="horizontal", variable=self.quality_var,
                command=lambda *_: self._update_quality_label(), showvalue=False
            )
        self.quality_slider.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4)
        self.quality_value_label = self._label(opts, "85")
        self.quality_value_label.grid(row=1, column=3, sticky="w", padx=4)

        # Row 2: resize and target size
        self.resize_on = tk.BooleanVar(value=False)
        self.resize_check = self._checkbox(opts, "Resize: max longest side (px)", self.resize_on,
                                           self._on_toggle_resize)
        self.resize_check.grid(row=2, column=0, sticky="w", padx=4, pady=(10, 0))

        self.resize_entry = self._entry(opts, default="1920", width=100)
        self.resize_entry.grid(row=2, column=1, sticky="w", padx=4, pady=(10, 0))
        self._set_widget_state(self.resize_entry, "disabled")

        self.target_on = tk.BooleanVar(value=False)
        self.target_check = self._checkbox(opts, "Target file size (KB)", self.target_on,
                                           self._on_toggle_target)
        self.target_check.grid(row=2, column=2, sticky="w", padx=4, pady=(10, 0))

        self.target_entry = self._entry(opts, default="500", width=100)
        self.target_entry.grid(row=2, column=3, sticky="w", padx=4, pady=(10, 0))
        self._set_widget_state(self.target_entry, "disabled")

        # Row 3: optimize toggles
        self.png_optimize_var = tk.BooleanVar(value=True)
        self.webp_lossless_var = tk.BooleanVar(value=False)
        self._checkbox(opts, "PNG optimize", self.png_optimize_var).grid(
            row=3, column=0, sticky="w", padx=4, pady=(10, 0)
        )
        self._checkbox(opts, "WebP lossless", self.webp_lossless_var).grid(
            row=3, column=1, sticky="w", padx=4, pady=(10, 0)
        )

        # Output folder
        out_frame = self._frame(self, padx=12, pady=6)
        out_frame.grid(row=3, column=0, sticky="ew")
        out_frame.grid_columnconfigure(1, weight=1)

        self._label(out_frame, "Output folder").grid(row=0, column=0, padx=(4, 8), sticky="w")
        self.output_var = tk.StringVar(value="(default: 'converted' next to each file)")
        self.output_entry = self._entry(out_frame, default=None, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self._button(out_frame, "Browse...", self._on_browse_output, kind="secondary").grid(
            row=0, column=2, padx=4
        )
        self._button(out_frame, "Reset", self._on_reset_output, kind="secondary").grid(
            row=0, column=3, padx=(4, 0)
        )

        # Action + progress
        action = self._frame(self, padx=12, pady=(6, 12))
        action.grid(row=4, column=0, sticky="ew")
        action.grid_columnconfigure(1, weight=1)

        self.convert_btn = self._button(action, "Convert", self._on_convert)
        self.convert_btn.grid(row=0, column=0, padx=(0, 12))

        if USING_CTK:
            self.progress = ctk.CTkProgressBar(action)
            self.progress.set(0)
        else:
            from tkinter import ttk
            self.progress = ttk.Progressbar(action, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=1, sticky="ew")

        self.status_var = tk.StringVar(value="Ready.")
        self.status_label = self._label(action, "")
        self.status_label.configure(textvariable=self.status_var)
        self.status_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self._on_format_change()

    # ----- Helpers for widgets -----
    def _frame(self, parent, padx=0, pady=0):
        if USING_CTK:
            f = ctk.CTkFrame(parent, fg_color="transparent")
        else:
            f = tk.Frame(parent)
        return f

    def _label(self, parent, text, muted: bool = False):
        if USING_CTK:
            color = ("gray50", "gray60") if muted else None
            if color:
                return ctk.CTkLabel(parent, text=text, text_color=color)
            return ctk.CTkLabel(parent, text=text)
        return tk.Label(parent, text=text, fg=("gray" if muted else "black"))

    def _button(self, parent, text, command, kind="primary"):
        if USING_CTK:
            if kind == "secondary":
                return ctk.CTkButton(parent, text=text, command=command,
                                     fg_color="transparent", border_width=1)
            return ctk.CTkButton(parent, text=text, command=command)
        return tk.Button(parent, text=text, command=command)

    def _entry(self, parent, default=None, width=None, textvariable=None):
        if USING_CTK:
            kwargs = {}
            if width:
                kwargs["width"] = width
            if textvariable is not None:
                kwargs["textvariable"] = textvariable
            e = ctk.CTkEntry(parent, **kwargs)
        else:
            kwargs = {}
            if width:
                kwargs["width"] = max(8, width // 8)
            if textvariable is not None:
                kwargs["textvariable"] = textvariable
            e = tk.Entry(parent, **kwargs)
        if default is not None and textvariable is None:
            e.insert(0, default)
        return e

    def _checkbox(self, parent, text, variable, command=None):
        if USING_CTK:
            return ctk.CTkCheckBox(parent, text=text, variable=variable, command=command)
        return tk.Checkbutton(parent, text=text, variable=variable, command=command)

    def _set_widget_state(self, widget, state: str) -> None:
        try:
            widget.configure(state=state)
        except Exception:
            pass

    # ----- Event handlers -----
    def _on_format_change(self) -> None:
        fmt = self.format_var.get()
        if fmt in LOSSY_FORMATS:
            self._set_widget_state(self.quality_slider, "normal")
        else:
            self._set_widget_state(self.quality_slider, "disabled")
        self._update_quality_label()

    def _update_quality_label(self) -> None:
        try:
            self.quality_value_label.configure(text=str(int(self.quality_var.get())))
        except Exception:
            pass

    def _on_toggle_resize(self) -> None:
        state = "normal" if self.resize_on.get() else "disabled"
        self._set_widget_state(self.resize_entry, state)

    def _on_toggle_target(self) -> None:
        state = "normal" if self.target_on.get() else "disabled"
        self._set_widget_state(self.target_entry, state)

    def _on_add_files(self) -> None:
        types = [
            ("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.heic *.heif"),
            ("All files", "*.*"),
        ]
        paths = filedialog.askopenfilenames(title="Select images", filetypes=types)
        if paths:
            self._add_paths([Path(p) for p in paths])

    def _on_clear_list(self) -> None:
        self.files.clear()
        self._render_list()

    def _on_browse_output(self) -> None:
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.output_var.set(folder)

    def _on_reset_output(self) -> None:
        self.output_var.set("(default: 'converted' next to each file)")

    def _on_drop_paths(self, raw_paths) -> None:
        paths: list[Path] = []
        for raw in raw_paths:
            try:
                p = raw.decode("mbcs") if isinstance(raw, bytes) else str(raw)
            except UnicodeDecodeError:
                p = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            path = Path(p)
            if path.is_dir():
                for child in path.rglob("*"):
                    if child.is_file() and is_supported(child):
                        paths.append(child)
            elif path.is_file():
                paths.append(path)
        if paths:
            self._add_paths(paths)

    # ----- File list management -----
    def _add_paths(self, paths: list[Path]) -> None:
        existing = {str(f.path.resolve()) for f in self.files}
        added = 0
        skipped = 0
        for p in paths:
            try:
                resolved = p.resolve()
            except OSError:
                skipped += 1
                continue
            key = str(resolved)
            if key in existing:
                continue
            if not p.is_file():
                skipped += 1
                continue
            if not is_supported(p):
                skipped += 1
                continue
            try:
                size = p.stat().st_size
            except OSError:
                skipped += 1
                continue
            self.files.append(FileEntry(resolved, size, _format_hint(p)))
            existing.add(key)
            added += 1
        self._render_list()
        if added or skipped:
            parts = []
            if added:
                parts.append(f"{added} added")
            if skipped:
                parts.append(f"{skipped} skipped (unsupported or unreadable)")
            self.status_var.set(", ".join(parts) + ".")

    def _render_list(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        if not self.files:
            self.empty_label = self._label(
                self.list_frame,
                "No files yet. Click \"Add files...\" or drop images here.",
                muted=True,
            )
            self.empty_label.grid(row=0, column=0, padx=20, pady=20)
            return
        for idx, entry in enumerate(self.files):
            row = self._frame(self.list_frame)
            row.grid(row=idx, column=0, sticky="ew", padx=4, pady=2)
            row.grid_columnconfigure(0, weight=1)
            entry.frame = row

            name_label = self._label(row, entry.path.name)
            name_label.grid(row=0, column=0, sticky="w", padx=(4, 8))

            meta_label = self._label(row, f"{human_size(entry.size)}  •  {entry.format_hint}",
                                     muted=True)
            meta_label.grid(row=0, column=1, sticky="e", padx=(0, 8))

            remove_btn = self._button(row, "Remove",
                                      lambda i=idx: self._remove_at(i),
                                      kind="secondary")
            remove_btn.grid(row=0, column=2, sticky="e", padx=(0, 4))

    def _remove_at(self, index: int) -> None:
        if 0 <= index < len(self.files):
            del self.files[index]
            self._render_list()

    # ----- Conversion driver -----
    def _gather_options(self) -> Optional[ConvertOptions]:
        out_format = self.format_var.get()
        quality = max(1, min(100, int(self.quality_var.get() or 85)))

        max_dim: Optional[int] = None
        if self.resize_on.get():
            raw = self.resize_entry.get().strip()
            try:
                max_dim = int(raw)
                if max_dim <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid resize value",
                                     "Max dimension must be a positive whole number.")
                return None

        target_kb: Optional[int] = None
        if self.target_on.get():
            raw = self.target_entry.get().strip()
            try:
                target_kb = int(raw)
                if target_kb <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid target size",
                                     "Target file size must be a positive whole number of KB.")
                return None
            if out_format not in LOSSY_FORMATS:
                messagebox.showwarning(
                    "Target size ignored",
                    f"Target file size only applies to lossy formats (JPG/WebP). It will be ignored for {out_format}.",
                )

        out_dir: Optional[Path] = None
        raw_out = self.output_var.get().strip()
        if raw_out and not raw_out.startswith("("):
            out_dir = Path(raw_out)

        # Warn about JPG/BMP transparency loss
        if out_format in {"JPG", "BMP"}:
            risky = [f.path.name for f in self.files
                     if f.path.suffix.lower() in {".png", ".webp", ".gif", ".heic", ".heif"}]
            if risky:
                ok = messagebox.askyesno(
                    "Transparency will be flattened",
                    f"{out_format} does not support transparency. Transparent areas in "
                    f"{len(risky)} file(s) will be filled with white. Continue?",
                )
                if not ok:
                    return None

        return ConvertOptions(
            out_format=out_format,
            quality=quality,
            max_dimension=max_dim,
            target_kb=target_kb,
            png_optimize=self.png_optimize_var.get(),
            webp_lossless=self.webp_lossless_var.get(),
            output_dir=out_dir,
        )

    def _on_convert(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo("No files", "Add at least one image first.")
            return
        opts = self._gather_options()
        if opts is None:
            return

        # Snapshot paths so the worker is independent of UI mutations
        paths = [f.path for f in self.files]
        self._set_progress(0.0)
        self.status_var.set(f"Converting 0 / {len(paths)}...")
        self.convert_btn.configure(state="disabled", text="Converting...")

        self._worker = threading.Thread(
            target=self._worker_run, args=(paths, opts), daemon=True
        )
        self._worker.start()

    def _worker_run(self, paths: list[Path], opts: ConvertOptions) -> None:
        results: list[ConvertResult] = []
        total = len(paths)
        for i, p in enumerate(paths, 1):
            try:
                res = convert_one(p, opts)
            except Exception as e:  # safety net
                res = ConvertResult(p, None, 0, 0, "error", f"Unexpected error: {e}")
            results.append(res)
            self._msg_queue.put(("progress", i, total, res))
        self._msg_queue.put(("done", results))

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, i, total, res = msg
                    self._set_progress(i / total)
                    short = res.src.name
                    if res.status == "done":
                        self.status_var.set(f"{i}/{total}: {short} — done")
                    elif res.status == "skipped":
                        self.status_var.set(f"{i}/{total}: {short} — skipped ({res.message})")
                    else:
                        self.status_var.set(f"{i}/{total}: {short} — error ({res.message})")
                elif kind == "done":
                    _, results = msg
                    self._on_batch_done(results)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _set_progress(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        if USING_CTK and hasattr(self.progress, "set"):
            self.progress.set(fraction)
        else:
            try:
                self.progress["value"] = fraction * 100
            except Exception:
                pass

    def _on_batch_done(self, results: list[ConvertResult]) -> None:
        self.convert_btn.configure(state="normal", text="Convert")
        total = len(results)
        done = [r for r in results if r.status == "done"]
        skipped = [r for r in results if r.status == "skipped"]
        errored = [r for r in results if r.status == "error"]

        orig_total = sum(r.original_bytes for r in done)
        new_total = sum(r.new_bytes for r in done)
        saved_pct = ((orig_total - new_total) / orig_total * 100) if orig_total else 0.0

        summary_lines = [
            f"Processed: {total}",
            f"Done: {len(done)}    Skipped: {len(skipped)}    Errored: {len(errored)}",
            "",
            f"Original size: {human_size(orig_total)}",
            f"New size:      {human_size(new_total)}",
            f"Saved:         {saved_pct:.1f}%",
        ]
        if done:
            out_dirs = sorted({str(r.dst.parent) for r in done if r.dst})
            if len(out_dirs) == 1:
                summary_lines.append("")
                summary_lines.append(f"Output: {out_dirs[0]}")

        problems = []
        for r in skipped + errored:
            problems.append(f"  • {r.src.name}: {r.message}")
        if problems:
            summary_lines.append("")
            summary_lines.append("Issues:")
            summary_lines.extend(problems[:20])
            if len(problems) > 20:
                summary_lines.append(f"  ... and {len(problems) - 20} more")

        self.status_var.set(
            f"Done. {len(done)}/{total} converted — saved {saved_pct:.1f}%."
        )
        messagebox.showinfo("Conversion complete", "\n".join(summary_lines))


def run() -> None:
    """Entry point. Wraps top-level errors with a friendly dialog."""
    try:
        app = App()
        app.mainloop()
    except Exception:
        tb = traceback.format_exc()
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Image Converter — unexpected error",
                "The application encountered an unexpected error and must close.\n\n"
                "Details:\n" + tb,
            )
            root.destroy()
        except Exception:
            sys.stderr.write(tb)


if __name__ == "__main__":
    run()
