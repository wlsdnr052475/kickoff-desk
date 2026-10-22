# -*- mode: python ; coding: utf-8 -*-
"""Build on Windows: python -m PyInstaller --clean --noconfirm kickoff-desk.spec.

Only package dependencies, tzdata, and the listed demo crests are bundled.
Never add the repository root, an environment file, local caches, or credentials
to ``datas``.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

project_root = Path(SPECPATH)
hiddenimports = ["keyring.backends.Windows"] if sys.platform == "win32" else []
crest_directory = project_root / "src" / "kickoff_desk" / "assets" / "crests"
crest_assets = ["5.png", "57.png", "65.png", "73.png", "81.png", "86.png", "SOURCES.md"]
crest_data = [
    (str(crest_directory / filename), "kickoff_desk/assets/crests") for filename in crest_assets
]

a = Analysis(
    [str(project_root / "scripts" / "launcher.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=collect_data_files("tzdata") + crest_data,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KickoffDesk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
