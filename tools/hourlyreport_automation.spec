# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


TOOLS_DIR = Path(SPECPATH)
ROOT = TOOLS_DIR.parent
APP_NAME = "hourlyreport_automation"

# This desktop app uses QtCore, QtGui, QtWidgets and local QtNetwork sockets.
# Keep qwindows and the image codecs (including WebP for the desktop pet), while
# removing optional runtimes collected transitively by PySide6 hooks.
QT_DROP_MARKERS = (
    "pyside6/opengl32sw.dll",
    "pyside6/qt6opengl.dll",
    "pyside6/qt6pdf.dll",
    "pyside6/qt6qml",
    "pyside6/qt6quick.dll",
    "pyside6/qt6virtualkeyboard.dll",
    "pyside6/plugins/generic/",
    "pyside6/plugins/imageformats/qpdf.dll",
    "pyside6/plugins/networkinformation/",
    "pyside6/plugins/platforminputcontexts/",
    "pyside6/plugins/platforms/qdirect2d.dll",
    "pyside6/plugins/platforms/qminimal.dll",
    "pyside6/plugins/platforms/qoffscreen.dll",
    "pyside6/plugins/tls/",
)


def keep_runtime_entry(entry):
    name = str(entry[0]).replace("\\", "/").lower()
    if name.endswith("pyside6/plugins/platforms/qwindows.dll"):
        return True
    return not any(marker in name for marker in QT_DROP_MARKERS)


a = Analysis(
    [str(ROOT / "gui" / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (
            str(
                ROOT
                / "modules"
                / "kst_local"
                / "resources"
                / "read_visitor_db.js"
            ),
            "modules/kst_local/resources",
        ),
    ],
    hiddenimports=["gui.update_dialog"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if keep_runtime_entry(entry)]
a.datas = [entry for entry in a.datas if keep_runtime_entry(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ROOT / "assets" / "app_icon.ico")],
)
