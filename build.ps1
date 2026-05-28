#requires -Version 5.1
<#
.SYNOPSIS
  Build ImageConverter.exe with PyInstaller.

.DESCRIPTION
  Wraps PyInstaller so the .exe always ends up with the same options and so
  the Tcl/Tk environment variables are set first. On some Python 3.13 +
  venv combinations on Windows, PyInstaller's tkinter probe fails with
  "tkinter installation is broken" because Tcl cannot locate its init.tcl
  scripts. Setting TCL_LIBRARY / TK_LIBRARY to the base interpreter's Tcl
  directory makes the probe succeed.

  Run this from the project root, with the venv already created and
  requirements installed.

.EXAMPLE
  .\build.ps1
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPython = Join-Path $root "venv\Scripts\python.exe"
$venvPyInstaller = Join-Path $root "venv\Scripts\pyinstaller.exe"

if (-not (Test-Path $venvPyInstaller)) {
    Write-Error "PyInstaller not found in venv. Run 'pip install -r requirements.txt' first."
}

# Locate Tcl/Tk script directories from the base interpreter, in case the
# venv's tkinter probe can't find them on its own.
$basePrefix = & $venvPython -c "import sys; print(sys.base_prefix)"
$tclCandidates = @(
    (Join-Path $basePrefix "tcl"),
    (Join-Path $basePrefix "lib"),
    $basePrefix
)
foreach ($root2 in $tclCandidates) {
    if (-not (Test-Path $root2)) { continue }
    $tclDir = Get-ChildItem $root2 -Directory -Filter "tcl[0-9]*" -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName "init.tcl") } |
        Select-Object -First 1
    $tkDir = Get-ChildItem $root2 -Directory -Filter "tk[0-9]*" -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName "tk.tcl") } |
        Select-Object -First 1
    if ($tclDir -and $tkDir) {
        $env:TCL_LIBRARY = $tclDir.FullName
        $env:TK_LIBRARY = $tkDir.FullName
        Write-Host "TCL_LIBRARY = $($tclDir.FullName)"
        Write-Host "TK_LIBRARY  = $($tkDir.FullName)"
        break
    }
}

Remove-Item -Recurse -Force build, dist, ImageConverter.spec -ErrorAction SilentlyContinue

& $venvPyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name ImageConverter `
    --collect-all customtkinter `
    --collect-all pillow_heif `
    main.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller exited with code $LASTEXITCODE"
}

$exe = Join-Path $root "dist\ImageConverter.exe"
if (Test-Path $exe) {
    $size = (Get-Item $exe).Length
    Write-Host ("Built: {0}  ({1:N1} MB)" -f $exe, ($size / 1MB))
} else {
    Write-Error "Build finished but $exe is missing."
}
