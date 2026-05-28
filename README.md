# Image Converter

A simple, friendly Windows desktop app for converting and compressing images in
batch. Drop in a pile of photos, pick a format and quality, and let it churn
through them.

## Features

- Batch conversion between JPG, PNG, WebP, BMP, TIFF, and GIF.
- Reads HEIC/HEIF (iPhone photos) via `pillow-heif`.
- Drag-and-drop or file-picker import.
- Quality slider for lossy formats (JPG / WebP).
- Optional resize to a max longest-side dimension.
- Optional target file size in KB — quality is auto-tuned down until the file
  fits.
- PNG optimize and WebP lossless toggles.
- Never overwrites originals: outputs go to a `converted` subfolder by default,
  or any folder you pick.
- Friendly errors: corrupt or unsupported files are skipped, not crashed on.
- Per-file progress and a summary of total size saved.

## Run from source

Requires Python 3.11+ on Windows.

```powershell
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Build the Windows .exe

After installing the requirements above, just run:

```powershell
.\build.ps1
```

The resulting executable is at `dist\ImageConverter.exe`. It is a single file
and does not require Python to be installed on the target machine.

`build.ps1` is a thin wrapper around PyInstaller that also points
`TCL_LIBRARY` / `TK_LIBRARY` at the base interpreter's Tcl directory. Without
that, PyInstaller 6.x + Python 3.13 + venv on Windows can decide that
"tkinter installation is broken" and silently exclude it from the build,
producing an .exe that fails at startup with `ModuleNotFoundError: tkinter`.

If you prefer to invoke PyInstaller directly, the equivalent command is:

```powershell
$env:TCL_LIBRARY = "$(python -c 'import sys; print(sys.base_prefix)')\tcl\tcl8.6"
$env:TK_LIBRARY  = "$(python -c 'import sys; print(sys.base_prefix)')\tcl\tk8.6"
pyinstaller --noconfirm --clean --onefile --windowed `
  --name "ImageConverter" `
  --collect-all customtkinter `
  --collect-all pillow_heif `
  main.py
```

## Project layout

```
image_converter/
  __init__.py
  app.py          # customtkinter GUI
  converter.py    # pure image conversion logic (no GUI deps)
main.py           # entry point
requirements.txt
README.md
.gitignore
```

The conversion logic in `image_converter/converter.py` is independent of the
GUI and can be reused as a small library.
