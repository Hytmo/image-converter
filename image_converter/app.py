"""customtkinter GUI for the image converter."""
from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Optional

import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
    USING_CTK = True
except ImportError:  # pragma: no cover
    ctk = tk  # type: ignore
    USING_CTK = False

from PIL import Image, ImageOps

from . import settings as user_settings
from .converter import (
    ConvertOptions,
    ConvertResult,
    LOSSY_FORMATS,
    convert_one,
    estimate_one,
    human_size,
    is_supported,
)
from .resources import resource_path

try:
    import windnd  # type: ignore
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


APP_TITLE = "Image Converter"
APP_VERSION = "1.0.0"
FORMATS = ["JPG", "PNG", "WEBP", "BMP", "TIFF", "GIF"]
THUMB_PX = 56
OUTER = 22
GUTTER = 14
CARD_PAD_X = 20
CARD_PAD_Y = 16

DEFAULT_OUTPUT_HINT = "(default: 'converted' next to each file)"

FORMAT_HINTS = {
    "JPG": "Best for photographs. Small files, lossy. No transparency.",
    "PNG": "Best for logos, screenshots, and images with transparency. Lossless, larger files.",
    "WEBP": "Modern format. Smaller than JPG/PNG at similar quality. Supports transparency. Great for the web.",
    "BMP": "Uncompressed bitmap. Very large files. Use only when a specific tool or system requires BMP.",
    "TIFF": "Professional editing, print, and archival. Lossless with rich metadata support.",
    "GIF": "Limited to 256 colours. Best for simple graphics or animations. Not for photographs.",
}

ESTIMATE_DEBOUNCE_MS = 350


class FileEntry:
    __slots__ = ("path", "size", "format_hint", "dimensions", "estimated_bytes",
                 "_thumb_pil", "_thumb_widget_ref", "_row_frame", "_meta_label")

    def __init__(self, path: Path, size: int, fmt: str):
        self.path = path
        self.size = size
        self.format_hint = fmt
        self.dimensions: Optional[tuple[int, int]] = None
        self.estimated_bytes: Optional[int] = None  # None=unknown/pending
        self._thumb_pil: Optional[Image.Image] = None  # None=pending, False=failed
        self._thumb_widget_ref: Any = None
        self._row_frame: Any = None
        self._meta_label: Any = None


def _format_hint(path: Path) -> str:
    return path.suffix.lstrip(".").upper() or "?"


def _make_placeholder_thumb(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (235, 235, 240, 255))
    return im


class App(ctk.CTk if USING_CTK else tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        if USING_CTK:
            ctk.set_appearance_mode("system")
            ctk.set_default_color_theme("blue")

        self.title(f"{APP_TITLE}")
        self.geometry("1020x780")
        self.minsize(900, 660)

        self._load_window_icon()

        # Persistent settings
        self.settings: dict[str, Any] = user_settings.load()

        # State
        self.files: list[FileEntry] = []
        self._worker: Optional[threading.Thread] = None
        self._msg_queue: "queue.Queue[tuple]" = queue.Queue()
        self._thumb_queue: "queue.Queue[FileEntry]" = queue.Queue()
        self._thumb_thread: Optional[threading.Thread] = None
        self._placeholder_pil = _make_placeholder_thumb(THUMB_PX)
        self._ctk_image_cache: dict[int, Any] = {}  # entry id -> CTkImage

        # Size-estimation worker
        self._estimation_queue: "queue.Queue[tuple]" = queue.Queue()
        self._estimation_thread: Optional[threading.Thread] = None
        self._estimation_generation = 0
        self._estimation_timer: Optional[str] = None

        self._build_layout()
        self._restore_settings_to_widgets()
        self._poll_queue()

        if DND_AVAILABLE:
            try:
                windnd.hook_dropfiles(self, func=self._on_drop_paths)
            except Exception:
                pass

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- Window icon -----
    def _load_window_icon(self) -> None:
        ico = resource_path("assets", "icon.ico")
        png = resource_path("assets", "icon.png")
        try:
            if ico.exists():
                self.iconbitmap(default=str(ico))
                return
        except Exception:
            pass
        try:
            if png.exists():
                photo = tk.PhotoImage(file=str(png))
                self.iconphoto(True, photo)
                # Keep a reference so it isn't garbage-collected
                self._icon_photo_ref = photo
        except Exception:
            pass

    # ----- Layout -----
    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)  # file list card expands

        self._build_header()
        self._build_toolbar()
        self._build_file_list()
        self._build_settings_card()
        self._build_output_card()
        self._build_footer()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=OUTER, pady=(OUTER, 4))
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header, text=APP_TITLE,
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        title.grid(row=0, column=0, sticky="w")

        sub = ctk.CTkLabel(
            header, text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray60"),
        )
        sub.grid(row=0, column=1, sticky="e", padx=(8, 0))

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=OUTER, pady=(0, GUTTER))
        bar.grid_columnconfigure(2, weight=1)

        ctk.CTkButton(
            bar, text="Add files…", command=self._on_add_files,
            width=130, height=36,
        ).grid(row=0, column=0, padx=(0, 8))

        ctk.CTkButton(
            bar, text="Clear list", command=self._on_clear_list,
            width=100, height=36,
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"),
        ).grid(row=0, column=1)

        dnd_hint = "Drag and drop images anywhere in the window" if DND_AVAILABLE \
            else "Install 'windnd' for drag-and-drop"
        ctk.CTkLabel(
            bar, text=dnd_hint,
            font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray60"),
        ).grid(row=0, column=2, sticky="e", padx=(8, 0))

    def _build_file_list(self) -> None:
        card = ctk.CTkFrame(self, corner_radius=10)
        card.grid(row=2, column=0, sticky="nsew", padx=OUTER, pady=(0, GUTTER))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="Files",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=CARD_PAD_X, pady=(CARD_PAD_Y, 6))

        self.list_frame = ctk.CTkScrollableFrame(
            card, fg_color="transparent",
        )
        self.list_frame.grid(row=1, column=0, sticky="nsew",
                             padx=CARD_PAD_X - 6, pady=(0, CARD_PAD_Y))
        self.list_frame.grid_columnconfigure(0, weight=1)

        self._empty_label = ctk.CTkLabel(
            self.list_frame,
            text="No files yet.\nClick \"Add files…\" or drop images here.",
            text_color=("gray50", "gray60"),
            justify="center",
        )
        self._empty_label.grid(row=0, column=0, padx=24, pady=40)

    def _build_settings_card(self) -> None:
        card = ctk.CTkFrame(self, corner_radius=10)
        card.grid(row=3, column=0, sticky="ew", padx=OUTER, pady=(0, GUTTER))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="Conversion settings",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=CARD_PAD_X, pady=(CARD_PAD_Y, 10))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew",
                  padx=CARD_PAD_X, pady=(0, CARD_PAD_Y))
        for c in range(4):
            body.grid_columnconfigure(c, weight=1, uniform="settings")

        # Row 0: labels
        ctk.CTkLabel(body, text="Format").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ctk.CTkLabel(body, text="Quality").grid(row=0, column=1, columnspan=3, sticky="w",
                                                pady=(0, 4), padx=(20, 0))

        # Row 1: controls
        self.format_var = tk.StringVar(value="JPG")
        self.format_menu = ctk.CTkOptionMenu(
            body, values=FORMATS, variable=self.format_var,
            command=lambda *_: self._on_format_change(),
            width=140,
        )
        self.format_menu.grid(row=1, column=0, sticky="ew", pady=(0, 14))

        slider_wrap = ctk.CTkFrame(body, fg_color="transparent")
        slider_wrap.grid(row=1, column=1, columnspan=3, sticky="ew",
                         pady=(0, 14), padx=(20, 0))
        slider_wrap.grid_columnconfigure(0, weight=1)

        self.quality_var = tk.IntVar(value=85)
        self.quality_slider = ctk.CTkSlider(
            slider_wrap, from_=1, to=100, number_of_steps=99,
            variable=self.quality_var,
            command=lambda *_: self._on_settings_changed(),
        )
        self.quality_slider.grid(row=0, column=0, sticky="ew")
        self.quality_value_label = ctk.CTkLabel(slider_wrap, text="85", width=32)
        self.quality_value_label.grid(row=0, column=1, padx=(10, 0))

        # Row 2: format use-case hint (spans all columns, wraps if needed)
        self.format_hint_label = ctk.CTkLabel(
            body, text="", anchor="w", justify="left",
            wraplength=720,
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray60"),
        )
        self.format_hint_label.grid(row=2, column=0, columnspan=4,
                                    sticky="ew", pady=(0, 16))

        # Row 3: resize
        self.resize_on = tk.BooleanVar(value=False)
        self.resize_check = ctk.CTkCheckBox(
            body, text="Resize: max longest side", variable=self.resize_on,
            command=self._on_toggle_resize,
        )
        self.resize_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=4)

        resize_row = ctk.CTkFrame(body, fg_color="transparent")
        resize_row.grid(row=3, column=2, columnspan=2, sticky="w", pady=4)
        self.resize_entry = ctk.CTkEntry(resize_row, width=90)
        self.resize_entry.insert(0, "1920")
        self.resize_entry.grid(row=0, column=0)
        self.resize_entry.bind("<KeyRelease>", self._on_settings_changed)
        ctk.CTkLabel(resize_row, text="px").grid(row=0, column=1, padx=(6, 0))
        self.resize_entry.configure(state="disabled")

        # Row 4: target size
        self.target_on = tk.BooleanVar(value=False)
        self.target_check = ctk.CTkCheckBox(
            body, text="Target file size", variable=self.target_on,
            command=self._on_toggle_target,
        )
        self.target_check.grid(row=4, column=0, columnspan=2, sticky="w", pady=4)

        target_row = ctk.CTkFrame(body, fg_color="transparent")
        target_row.grid(row=4, column=2, columnspan=2, sticky="w", pady=4)
        self.target_entry = ctk.CTkEntry(target_row, width=90)
        self.target_entry.insert(0, "500")
        self.target_entry.grid(row=0, column=0)
        self.target_entry.bind("<KeyRelease>", self._on_settings_changed)
        ctk.CTkLabel(target_row, text="KB").grid(row=0, column=1, padx=(6, 0))
        self.target_entry.configure(state="disabled")

        # Row 5: toggles
        toggles = ctk.CTkFrame(body, fg_color="transparent")
        toggles.grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))
        self.png_optimize_var = tk.BooleanVar(value=True)
        self.webp_lossless_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(toggles, text="PNG optimize",
                        variable=self.png_optimize_var,
                        command=self._on_settings_changed).grid(
            row=0, column=0, padx=(0, 24)
        )
        ctk.CTkCheckBox(toggles, text="WebP lossless",
                        variable=self.webp_lossless_var,
                        command=self._on_settings_changed).grid(
            row=0, column=1
        )

    def _build_output_card(self) -> None:
        card = ctk.CTkFrame(self, corner_radius=10)
        card.grid(row=4, column=0, sticky="ew", padx=OUTER, pady=(0, GUTTER))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="Output folder",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=CARD_PAD_X, pady=(CARD_PAD_Y, 8))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew",
                 padx=CARD_PAD_X, pady=(0, CARD_PAD_Y))
        row.grid_columnconfigure(0, weight=1)

        self.output_var = tk.StringVar(value=DEFAULT_OUTPUT_HINT)
        self.output_entry = ctk.CTkEntry(row, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        ctk.CTkButton(
            row, text="Browse…", command=self._on_browse_output,
            width=110, fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"),
        ).grid(row=0, column=1, padx=(0, 6))

        ctk.CTkButton(
            row, text="Reset", command=self._on_reset_output,
            width=80, fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"),
        ).grid(row=0, column=2)

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=OUTER, pady=(0, OUTER))
        footer.grid_columnconfigure(1, weight=1)

        self.convert_btn = ctk.CTkButton(
            footer, text="Convert", command=self._on_convert,
            width=160, height=44,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.convert_btn.grid(row=0, column=0, padx=(0, 16))

        bar_col = ctk.CTkFrame(footer, fg_color="transparent")
        bar_col.grid(row=0, column=1, sticky="ew")
        bar_col.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(bar_col, height=12)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew")

        self.status_var = tk.StringVar(value="Ready.")
        ctk.CTkLabel(
            bar_col, textvariable=self.status_var,
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray70"),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))

    # ----- Settings persistence -----
    def _restore_settings_to_widgets(self) -> None:
        s = self.settings
        fmt = s.get("format")
        if isinstance(fmt, str) and fmt.upper() in FORMATS:
            self.format_var.set(fmt.upper())
        q = s.get("quality")
        if isinstance(q, int) and 1 <= q <= 100:
            self.quality_var.set(q)
        if isinstance(s.get("max_dimension"), int):
            self.resize_entry.configure(state="normal")
            self.resize_entry.delete(0, "end")
            self.resize_entry.insert(0, str(s["max_dimension"]))
            self.resize_entry.configure(state="disabled")
        if isinstance(s.get("target_kb"), int):
            self.target_entry.configure(state="normal")
            self.target_entry.delete(0, "end")
            self.target_entry.insert(0, str(s["target_kb"]))
            self.target_entry.configure(state="disabled")
        for key, var in (
            ("resize_on", self.resize_on),
            ("target_on", self.target_on),
            ("png_optimize", self.png_optimize_var),
            ("webp_lossless", self.webp_lossless_var),
        ):
            if isinstance(s.get(key), bool):
                var.set(s[key])
        out = s.get("output_folder")
        if isinstance(out, str) and out:
            self.output_var.set(out)
        self._on_format_change()
        self._on_toggle_resize()
        self._on_toggle_target()
        self._update_quality_label()

    def _gather_settings_snapshot(self) -> dict[str, Any]:
        out = self.output_var.get().strip()
        try:
            max_dim = int(self.resize_entry.get().strip())
        except ValueError:
            max_dim = None
        try:
            target_kb = int(self.target_entry.get().strip())
        except ValueError:
            target_kb = None
        return {
            "format": self.format_var.get(),
            "quality": int(self.quality_var.get()),
            "max_dimension": max_dim,
            "target_kb": target_kb,
            "resize_on": bool(self.resize_on.get()),
            "target_on": bool(self.target_on.get()),
            "png_optimize": bool(self.png_optimize_var.get()),
            "webp_lossless": bool(self.webp_lossless_var.get()),
            "output_folder": "" if out.startswith("(") else out,
        }

    def _save_settings(self) -> None:
        try:
            data = self._gather_settings_snapshot()
            self.settings.update(data)
            user_settings.save(self.settings)
        except Exception:
            pass

    def _on_close(self) -> None:
        self._save_settings()
        self.destroy()

    # ----- Small helpers -----
    def _on_format_change(self) -> None:
        fmt = self.format_var.get()
        state = "normal" if fmt in LOSSY_FORMATS else "disabled"
        try:
            self.quality_slider.configure(state=state)
        except Exception:
            pass
        self._update_format_hint(fmt)
        self._on_settings_changed()

    def _update_format_hint(self, fmt: str) -> None:
        try:
            self.format_hint_label.configure(text=FORMAT_HINTS.get(fmt, ""))
        except Exception:
            pass

    def _update_quality_label(self) -> None:
        try:
            self.quality_value_label.configure(text=str(int(self.quality_var.get())))
        except Exception:
            pass

    def _on_toggle_resize(self) -> None:
        state = "normal" if self.resize_on.get() else "disabled"
        try:
            self.resize_entry.configure(state=state)
        except Exception:
            pass
        self._on_settings_changed()

    def _on_toggle_target(self) -> None:
        state = "normal" if self.target_on.get() else "disabled"
        try:
            self.target_entry.configure(state=state)
        except Exception:
            pass
        self._on_settings_changed()

    # ----- Settings change pipeline + estimation -----
    def _on_settings_changed(self, *_event) -> None:
        """Triggered whenever any setting that affects output size changes.

        Updates the visible quality value, invalidates any current per-file
        estimates, and schedules a fresh estimation pass after a short
        debounce so we don't thrash while a slider is being dragged.
        """
        self._update_quality_label()
        self._estimation_generation += 1
        gen = self._estimation_generation
        for entry in self.files:
            entry.estimated_bytes = None
            if entry._meta_label is not None:
                self._refresh_meta_label(entry)
        if self._estimation_timer is not None:
            try:
                self.after_cancel(self._estimation_timer)
            except Exception:
                pass
        self._estimation_timer = self.after(
            ESTIMATE_DEBOUNCE_MS, lambda g=gen: self._kick_off_estimation(g)
        )

    def _snapshot_options_for_estimate(self) -> Optional[ConvertOptions]:
        fmt = self.format_var.get()
        try:
            q = max(1, min(100, int(self.quality_var.get())))
        except Exception:
            q = 85
        max_dim: Optional[int] = None
        if self.resize_on.get():
            try:
                v = int(self.resize_entry.get().strip())
                if v > 0:
                    max_dim = v
            except ValueError:
                return None
        target_kb: Optional[int] = None
        if self.target_on.get():
            try:
                v = int(self.target_entry.get().strip())
                if v > 0:
                    target_kb = v
            except ValueError:
                return None
        return ConvertOptions(
            out_format=fmt,
            quality=q,
            max_dimension=max_dim,
            target_kb=target_kb,
            png_optimize=bool(self.png_optimize_var.get()),
            webp_lossless=bool(self.webp_lossless_var.get()),
        )

    def _kick_off_estimation(self, gen: int) -> None:
        self._estimation_timer = None
        if gen != self._estimation_generation:
            return
        opts = self._snapshot_options_for_estimate()
        if opts is None:
            return  # invalid numeric input — wait for valid values
        # Drain stale items
        while True:
            try:
                self._estimation_queue.get_nowait()
            except queue.Empty:
                break
        for entry in self.files:
            self._estimation_queue.put((entry, gen, opts))
        if not self._estimation_thread or not self._estimation_thread.is_alive():
            self._estimation_thread = threading.Thread(
                target=self._estimation_worker, daemon=True,
            )
            self._estimation_thread.start()

    def _estimation_worker(self) -> None:
        while True:
            try:
                entry, gen, opts = self._estimation_queue.get(timeout=0.3)
            except queue.Empty:
                return
            if gen != self._estimation_generation:
                continue
            try:
                est = estimate_one(entry.path, opts)
            except Exception:
                est = None
            self._msg_queue.put(("estimate", id(entry), gen, est))

    def _on_add_files(self) -> None:
        types = [
            ("Images",
             "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.heic *.heif"),
            ("All files", "*.*"),
        ]
        initial = self.settings.get("last_input_folder") or ""
        kwargs = {"title": "Select images", "filetypes": types}
        if initial and Path(initial).is_dir():
            kwargs["initialdir"] = initial
        paths = filedialog.askopenfilenames(**kwargs)
        if paths:
            self.settings["last_input_folder"] = str(Path(paths[0]).parent)
            user_settings.save(self.settings)
            self._add_paths([Path(p) for p in paths])

    def _on_clear_list(self) -> None:
        self.files.clear()
        self._ctk_image_cache.clear()
        self._render_list()

    def _on_browse_output(self) -> None:
        current = self.output_var.get().strip()
        kwargs = {"title": "Choose output folder"}
        if current and not current.startswith("(") and Path(current).is_dir():
            kwargs["initialdir"] = current
        elif self.settings.get("output_folder"):
            d = self.settings["output_folder"]
            if Path(d).is_dir():
                kwargs["initialdir"] = d
        folder = filedialog.askdirectory(**kwargs)
        if folder:
            self.output_var.set(folder)
            self.settings["output_folder"] = folder
            user_settings.save(self.settings)

    def _on_reset_output(self) -> None:
        self.output_var.set(DEFAULT_OUTPUT_HINT)
        self.settings["output_folder"] = ""
        user_settings.save(self.settings)

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

    # ----- File list -----
    def _add_paths(self, paths: list[Path]) -> None:
        existing = {str(f.path.resolve()) for f in self.files}
        added: list[FileEntry] = []
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
            if not p.is_file() or not is_supported(p):
                skipped += 1
                continue
            try:
                size = p.stat().st_size
            except OSError:
                skipped += 1
                continue
            entry = FileEntry(resolved, size, _format_hint(p))
            self.files.append(entry)
            existing.add(key)
            added.append(entry)

        self._render_list()
        if added:
            self._enqueue_thumbs(added)
            self._enqueue_estimates(added)
        if added or skipped:
            parts = []
            if added:
                parts.append(f"{len(added)} added")
            if skipped:
                parts.append(f"{skipped} skipped")
            self.status_var.set(", ".join(parts) + ".")

    def _render_list(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        if not self.files:
            self._empty_label = ctk.CTkLabel(
                self.list_frame,
                text="No files yet.\nClick \"Add files…\" or drop images here.",
                text_color=("gray50", "gray60"),
                justify="center",
            )
            self._empty_label.grid(row=0, column=0, padx=24, pady=40)
            return
        for idx, entry in enumerate(self.files):
            self._render_row(idx, entry)

    def _render_row(self, idx: int, entry: FileEntry) -> None:
        row = ctk.CTkFrame(self.list_frame, corner_radius=8,
                           fg_color=("gray94", "gray22"))
        row.grid(row=idx, column=0, sticky="ew", padx=4, pady=3)
        row.grid_columnconfigure(1, weight=1)
        entry._row_frame = row

        thumb_img = self._build_ctk_image_for(entry)
        thumb_lbl = ctk.CTkLabel(row, image=thumb_img, text="")
        thumb_lbl.grid(row=0, column=0, rowspan=2, padx=(10, 12), pady=10)
        entry._thumb_widget_ref = thumb_lbl

        ctk.CTkLabel(
            row, text=entry.path.name, anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=1, sticky="ew", pady=(10, 0))

        meta_label = ctk.CTkLabel(
            row, text="", anchor="w",
            text_color=("gray45", "gray65"),
            font=ctk.CTkFont(size=11),
        )
        meta_label.grid(row=1, column=1, sticky="ew", pady=(0, 10))
        entry._meta_label = meta_label
        self._refresh_meta_label(entry)

        ctk.CTkButton(
            row, text="✕", command=lambda i=idx: self._remove_at(i),
            width=32, height=32,
            fg_color="transparent",
            text_color=("gray35", "gray70"),
            hover_color=("gray80", "gray30"),
        ).grid(row=0, column=2, rowspan=2, padx=(8, 10), pady=10)

    def _refresh_meta_label(self, entry: FileEntry) -> None:
        if entry._meta_label is None:
            return
        parts = []
        if isinstance(entry.estimated_bytes, int):
            orig = entry.size
            est = entry.estimated_bytes
            if orig > 0:
                delta_pct = (orig - est) / orig * 100
                sign = "-" if delta_pct >= 0 else "+"
                parts.append(
                    f"{human_size(orig)} → {human_size(est)}  "
                    f"({sign}{abs(delta_pct):.0f}%)"
                )
            else:
                parts.append(f"{human_size(orig)} → {human_size(est)}")
        else:
            parts.append(human_size(entry.size))
        parts.append(entry.format_hint)
        if entry.dimensions:
            w, h = entry.dimensions
            parts.append(f"{w} × {h}")
        text = "  •  ".join(parts)
        try:
            entry._meta_label.configure(text=text)
        except Exception:
            pass

    def _build_ctk_image_for(self, entry: FileEntry):
        pil = entry._thumb_pil if isinstance(entry._thumb_pil, Image.Image) \
            else self._placeholder_pil
        img = ctk.CTkImage(light_image=pil, dark_image=pil,
                           size=(THUMB_PX, THUMB_PX))
        self._ctk_image_cache[id(entry)] = img  # keep reference
        return img

    def _remove_at(self, index: int) -> None:
        if 0 <= index < len(self.files):
            entry = self.files.pop(index)
            self._ctk_image_cache.pop(id(entry), None)
            self._render_list()

    def _enqueue_estimates(self, entries: list[FileEntry]) -> None:
        opts = self._snapshot_options_for_estimate()
        if opts is None:
            return
        gen = self._estimation_generation
        for e in entries:
            self._estimation_queue.put((e, gen, opts))
        if not self._estimation_thread or not self._estimation_thread.is_alive():
            self._estimation_thread = threading.Thread(
                target=self._estimation_worker, daemon=True,
            )
            self._estimation_thread.start()

    # ----- Thumbnail worker -----
    def _enqueue_thumbs(self, entries: list[FileEntry]) -> None:
        for e in entries:
            self._thumb_queue.put(e)
        if not self._thumb_thread or not self._thumb_thread.is_alive():
            self._thumb_thread = threading.Thread(
                target=self._thumb_worker, daemon=True,
            )
            self._thumb_thread.start()

    def _thumb_worker(self) -> None:
        while True:
            try:
                entry = self._thumb_queue.get(timeout=0.3)
            except queue.Empty:
                return
            try:
                with Image.open(entry.path) as im:
                    im = ImageOps.exif_transpose(im)
                    w, h = im.size
                    entry.dimensions = (w, h)
                    im.thumbnail((THUMB_PX * 2, THUMB_PX * 2),
                                 Image.Resampling.LANCZOS)
                    if im.mode != "RGBA":
                        im = im.convert("RGBA")
                    # Center on a square placeholder for consistent layout
                    square = Image.new("RGBA", (THUMB_PX * 2, THUMB_PX * 2),
                                       (0, 0, 0, 0))
                    iw, ih = im.size
                    ox = (THUMB_PX * 2 - iw) // 2
                    oy = (THUMB_PX * 2 - ih) // 2
                    square.paste(im, (ox, oy), im)
                    entry._thumb_pil = square
            except Exception:
                entry._thumb_pil = False  # sentinel for failed
            self._msg_queue.put(("thumb", id(entry)))

    # ----- Conversion -----
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
                messagebox.showerror(
                    "Invalid resize value",
                    "Max dimension must be a positive whole number.",
                )
                return None

        target_kb: Optional[int] = None
        if self.target_on.get():
            raw = self.target_entry.get().strip()
            try:
                target_kb = int(raw)
                if target_kb <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Invalid target size",
                    "Target file size must be a positive whole number of KB.",
                )
                return None
            if out_format not in LOSSY_FORMATS:
                messagebox.showwarning(
                    "Target size ignored",
                    f"Target file size only applies to lossy formats (JPG/WebP). "
                    f"It will be ignored for {out_format}.",
                )

        out_dir: Optional[Path] = None
        raw_out = self.output_var.get().strip()
        if raw_out and not raw_out.startswith("("):
            out_dir = Path(raw_out)

        if out_format in {"JPG", "BMP"}:
            risky = [f.path.name for f in self.files
                     if f.path.suffix.lower() in
                     {".png", ".webp", ".gif", ".heic", ".heif"}]
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
        self._save_settings()

        paths = [f.path for f in self.files]
        self._set_progress(0.0)
        self.status_var.set(f"Converting 0 / {len(paths)}…")
        self.convert_btn.configure(state="disabled", text="Converting…")

        self._worker = threading.Thread(
            target=self._worker_run, args=(paths, opts), daemon=True,
        )
        self._worker.start()

    def _worker_run(self, paths: list[Path], opts: ConvertOptions) -> None:
        results: list[ConvertResult] = []
        total = len(paths)
        for i, p in enumerate(paths, 1):
            try:
                res = convert_one(p, opts)
            except Exception as e:
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
                        self.status_var.set(
                            f"{i}/{total}: {short} — skipped ({res.message})"
                        )
                    else:
                        self.status_var.set(
                            f"{i}/{total}: {short} — error ({res.message})"
                        )
                elif kind == "done":
                    _, results = msg
                    self._on_batch_done(results)
                elif kind == "thumb":
                    self._update_thumb_for(msg[1])
                elif kind == "estimate":
                    _, entry_id, gen, est = msg
                    if gen != self._estimation_generation:
                        continue
                    entry = next((f for f in self.files if id(f) == entry_id), None)
                    if entry is not None:
                        entry.estimated_bytes = est if isinstance(est, int) else None
                        self._refresh_meta_label(entry)
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _update_thumb_for(self, entry_id: int) -> None:
        entry = next((f for f in self.files if id(f) == entry_id), None)
        if entry is None:
            return
        widget = entry._thumb_widget_ref
        if widget is None or not widget.winfo_exists():
            return
        new_img = self._build_ctk_image_for(entry)
        try:
            widget.configure(image=new_img)
        except Exception:
            return
        # Refresh the meta line so the newly-known dimensions appear
        self._refresh_meta_label(entry)

    def _set_progress(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        try:
            self.progress.set(fraction)
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

        lines = [
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
                lines.append("")
                lines.append(f"Output: {out_dirs[0]}")

        problems = []
        for r in skipped + errored:
            problems.append(f"  • {r.src.name}: {r.message}")
        if problems:
            lines.append("")
            lines.append("Issues:")
            lines.extend(problems[:20])
            if len(problems) > 20:
                lines.append(f"  … and {len(problems) - 20} more")

        self.status_var.set(
            f"Done. {len(done)}/{total} converted — saved {saved_pct:.1f}%."
        )
        messagebox.showinfo("Conversion complete", "\n".join(lines))


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
