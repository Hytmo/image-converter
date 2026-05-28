r"""Generate the application icon as icon.ico and icon.png.

Run from project root:
    py -3 assets\make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSET_DIR = Path(__file__).parent
ICON_ICO = ASSET_DIR / "icon.ico"
ICON_PNG = ASSET_DIR / "icon.png"


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    return mask


def _vertical_gradient(size: int, c1: tuple, c2: tuple) -> Image.Image:
    grad = Image.new("RGBA", (size, size), c1)
    draw = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / max(1, size - 1)
        draw.line([(0, y), (size, y)], fill=_lerp(c1, c2, t))
    return grad


def make_icon(size: int = 512) -> Image.Image:
    # Background: rounded square with indigo->violet gradient
    indigo = (84, 92, 232, 255)
    violet = (170, 88, 220, 255)
    grad = _vertical_gradient(size, indigo, violet)

    radius = int(size * 0.22)
    mask = _rounded_mask(size, radius)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg.paste(grad, (0, 0), mask)

    # Inner picture frame
    pad = int(size * 0.20)
    inner_box = [pad, pad, size - pad, size - pad]
    inner_radius = int(size * 0.05)
    line_w = max(2, int(size * 0.024))

    frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fd = ImageDraw.Draw(frame)
    fd.rounded_rectangle(
        inner_box, radius=inner_radius, outline=(255, 255, 255, 255), width=line_w
    )

    # Sun + mountains inside the frame
    content = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cd = ImageDraw.Draw(content)

    sun_r = int(size * 0.06)
    sun_cx, sun_cy = int(size * 0.36), int(size * 0.40)
    cd.ellipse(
        [sun_cx - sun_r, sun_cy - sun_r, sun_cx + sun_r, sun_cy + sun_r],
        fill=(255, 220, 120, 255),
    )

    base_y = size - pad - line_w - 1
    # Back mountain (lighter, taller)
    cd.polygon(
        [
            (pad + line_w, base_y),
            (int(size * 0.46), int(size * 0.50)),
            (int(size * 0.68), base_y),
        ],
        fill=(225, 230, 245, 255),
    )
    # Front mountain (white, lower)
    cd.polygon(
        [
            (int(size * 0.50), base_y),
            (int(size * 0.70), int(size * 0.56)),
            (size - pad - line_w, base_y),
        ],
        fill=(255, 255, 255, 255),
    )

    # Compose layers and re-mask to keep edges crisp
    out = Image.alpha_composite(bg, content)
    out = Image.alpha_composite(out, frame)

    final = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    final.paste(out, (0, 0), mask)
    return final


def main() -> None:
    base = make_icon(512)
    base.save(ICON_PNG, format="PNG")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base.save(ICON_ICO, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"Wrote {ICON_PNG}")
    print(f"Wrote {ICON_ICO}")


if __name__ == "__main__":
    main()
