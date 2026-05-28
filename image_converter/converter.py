"""Image conversion and compression logic. No GUI dependencies."""
from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except Exception:
    HEIF_SUPPORTED = False


SUPPORTED_INPUT_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff",
    ".gif", ".heic", ".heif",
}

FORMAT_TO_EXT = {
    "JPG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tiff",
    "GIF": ".gif",
}

FORMAT_TO_PIL = {
    "JPG": "JPEG",
    "PNG": "PNG",
    "WEBP": "WEBP",
    "BMP": "BMP",
    "TIFF": "TIFF",
    "GIF": "GIF",
}

LOSSY_FORMATS = {"JPG", "WEBP"}
TRANSPARENT_INCAPABLE = {"JPG", "BMP"}


@dataclass
class ConvertOptions:
    out_format: str = "JPG"
    quality: int = 85
    max_dimension: Optional[int] = None
    target_kb: Optional[int] = None
    png_optimize: bool = True
    webp_lossless: bool = False
    output_dir: Optional[Path] = None
    flatten_bg: tuple = (255, 255, 255)


@dataclass
class ConvertResult:
    src: Path
    dst: Optional[Path]
    original_bytes: int
    new_bytes: int
    status: str
    message: str = ""


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_INPUT_EXTS


def _prepare_image(img: Image.Image, out_format: str, bg: tuple) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if out_format in TRANSPARENT_INCAPABLE:
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, bg)
            mask = img.split()[-1]
            base = img.convert("RGBA") if img.mode == "LA" else img
            background.paste(base, mask=mask)
            return background
        if img.mode == "P" and "transparency" in img.info:
            base = img.convert("RGBA")
            background = Image.new("RGB", base.size, bg)
            background.paste(base, mask=base.split()[-1])
            return background
        if img.mode != "RGB":
            return img.convert("RGB")
        return img
    if out_format == "GIF":
        return img if img.mode in ("P", "L") else img.convert("P", palette=Image.Palette.ADAPTIVE)
    if out_format == "PNG":
        if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            return img.convert("RGBA")
        return img
    if out_format == "WEBP":
        return img if img.mode in ("RGB", "RGBA") else img.convert("RGBA")
    if out_format == "TIFF":
        return img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGB")
    if out_format == "BMP":
        return img if img.mode == "RGB" else img.convert("RGB")
    return img


def _resize(img: Image.Image, max_dim: int) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return img
    scale = max_dim / longest
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(new_size, Image.Resampling.LANCZOS)


def _save_kwargs(out_format: str, quality: int, opts: ConvertOptions) -> dict:
    pil_fmt = FORMAT_TO_PIL[out_format]
    kw: dict = {"format": pil_fmt}
    if out_format == "JPG":
        kw.update(quality=quality, optimize=True, progressive=True)
    elif out_format == "WEBP":
        if opts.webp_lossless:
            kw.update(lossless=True, quality=100, method=6)
        else:
            kw.update(quality=quality, method=6)
    elif out_format == "PNG":
        kw.update(optimize=opts.png_optimize, compress_level=9 if opts.png_optimize else 6)
    elif out_format == "TIFF":
        kw.update(compression="tiff_lzw")
    elif out_format == "GIF":
        kw.update(optimize=True)
    return kw


def _save_to_bytes(img: Image.Image, out_format: str, quality: int, opts: ConvertOptions) -> bytes:
    kw = _save_kwargs(out_format, quality, opts)
    fmt = kw.pop("format")
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def _binary_search_quality(
    img: Image.Image, out_format: str, target_bytes: int, max_q: int, opts: ConvertOptions
) -> tuple[int, bytes]:
    """Find the highest quality that produces output <= target_bytes.

    Falls back to the lowest-quality encode if even q=1 exceeds the target.
    """
    lo, hi = 1, max(1, min(100, max_q))
    best: Optional[tuple[int, bytes]] = None
    while lo <= hi:
        mid = (lo + hi) // 2
        data = _save_to_bytes(img, out_format, mid, opts)
        if len(data) <= target_bytes:
            best = (mid, data)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        data = _save_to_bytes(img, out_format, 1, opts)
        return (1, data)
    return best


def _unique_output_path(base: Path) -> Path:
    if not base.exists():
        return base
    stem, suffix = base.stem, base.suffix
    parent = base.parent
    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def convert_one(src: Path, opts: ConvertOptions) -> ConvertResult:
    """Convert a single image. Never raises; returns a result with status."""
    try:
        src = Path(src)
        original_bytes = src.stat().st_size
    except OSError as e:
        return ConvertResult(Path(src), None, 0, 0, "error", f"Cannot read file: {e}")

    if not is_supported(src):
        return ConvertResult(src, None, original_bytes, 0, "skipped",
                             f"Unsupported extension: {src.suffix}")

    out_format = opts.out_format.upper()
    if out_format not in FORMAT_TO_EXT:
        return ConvertResult(src, None, original_bytes, 0, "error",
                             f"Unknown output format: {out_format}")

    try:
        with Image.open(src) as raw:
            raw.load()
            img = _prepare_image(raw, out_format, opts.flatten_bg)
    except UnidentifiedImageError:
        return ConvertResult(src, None, original_bytes, 0, "skipped",
                             "Not a recognizable image")
    except (OSError, ValueError) as e:
        return ConvertResult(src, None, original_bytes, 0, "skipped",
                             f"Could not open: {e}")

    if opts.max_dimension and opts.max_dimension > 0:
        img = _resize(img, int(opts.max_dimension))

    out_dir = opts.output_dir if opts.output_dir else (src.parent / "converted")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return ConvertResult(src, None, original_bytes, 0, "error",
                             f"Cannot create output folder: {e}")

    out_ext = FORMAT_TO_EXT[out_format]
    out_path = out_dir / (src.stem + out_ext)
    if out_path.resolve() == src.resolve():
        out_path = out_dir / (src.stem + "_converted" + out_ext)
    out_path = _unique_output_path(out_path)

    try:
        if (
            opts.target_kb
            and opts.target_kb > 0
            and out_format in LOSSY_FORMATS
            and not (out_format == "WEBP" and opts.webp_lossless)
        ):
            target_bytes = int(opts.target_kb) * 1024
            _, data = _binary_search_quality(img, out_format, target_bytes, opts.quality, opts)
            out_path.write_bytes(data)
        else:
            kw = _save_kwargs(out_format, opts.quality, opts)
            fmt = kw.pop("format")
            img.save(out_path, format=fmt, **kw)
    except (OSError, ValueError) as e:
        return ConvertResult(src, None, original_bytes, 0, "error",
                             f"Could not save: {e}")

    try:
        new_bytes = out_path.stat().st_size
    except OSError:
        new_bytes = 0

    return ConvertResult(src, out_path, original_bytes, new_bytes, "done")
