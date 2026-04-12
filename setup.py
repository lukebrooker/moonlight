"""py2app build script for Moonlight.

Usage (from project root, with venv active):
    python setup.py py2app -A     # alias mode (dev-friendly, this machine only)
    python setup.py py2app        # full standalone build for distribution

Standalone builds are the default for release. Alias mode is useful when
iterating locally because it symlinks into the venv instead of freezing
dependencies into the bundle.
"""

import os
import re

from setuptools import setup

APP = ["moonlight_app.py"]

# Version comes from CI (tag) or dev fallback. Strip a leading "v" so tags
# like "v1.2.3" produce plist values of "1.2.3".
_raw_version = os.environ.get("MOONLIGHT_VERSION", "0.0.0-dev")
VERSION = _raw_version[1:] if _raw_version.startswith("v") else _raw_version


def _to_pep440(version: str) -> str:
    """Coerce a SemVer-ish string into a PEP 440-compliant version.

    setuptools rejects strings like "1.0.0-rc1" or "0.0.0-dev". py2app and
    macOS don't care about the version string shape, but `setup(version=...)`
    runs through `packaging.Version`. Try the version as-is first; if that
    fails, translate common SemVer suffixes so users can keep using
    natural-looking tags.
    """
    from packaging.version import InvalidVersion, Version

    try:
        return str(Version(version))
    except InvalidVersion:
        pass

    m = re.match(r"^(\d+(?:\.\d+)*)-(.+)$", version)
    if not m:
        return "0.0.0"
    base, suffix = m.group(1), m.group(2)
    if suffix in ("dev", "dev0"):
        return f"{base}.dev0"
    pre = re.match(r"^(rc|a|b)(\d+)?$", suffix)
    if pre:
        return f"{base}{pre.group(1)}{pre.group(2) or '0'}"
    safe = re.sub(r"[^a-zA-Z0-9]", ".", suffix).strip(".")
    return f"{base}+{safe}" if safe else base


# setuptools wants PEP 440; the bundle Info.plist gets the raw version so
# the user-visible string in About / Get Info matches the release tag.
SETUP_VERSION = _to_pep440(VERSION)

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "icon.icns",
    "arch": "arm64",
    "semi_standalone": False,
    "strip": True,
    "optimize": 0,
    "plist": {
        "CFBundleName": "Moonlight",
        "CFBundleDisplayName": "Moonlight",
        "CFBundleIdentifier": "com.moonlight.controller",
        "CFBundleVersion": VERSION,
        "CFBundleShortVersionString": VERSION,
        "LSUIElement": True,  # Menu bar app, no dock icon by default
        "LSMinimumSystemVersion": "11.0",
        "NSBluetoothAlwaysUsageDescription":
            "Moonlight needs Bluetooth to control your Moonside Halo lamp.",
        "NSBluetoothPeripheralUsageDescription":
            "Moonlight needs Bluetooth to control your Moonside Halo lamp.",
        "NSMicrophoneUsageDescription":
            "Moonlight uses the microphone for music visualization mode.",
        "NSHighResolutionCapable": True,
    },
    "packages": [
        "rumps",
        "bleak",
        "sounddevice",
        # CRITICAL: ships libportaudio.dylib alongside sounddevice
        "_sounddevice_data",
        "numpy",
        "objc",
        "Foundation",
        # bleak loads CoreBluetooth lazily at runtime; modulegraph misses it
        # unless we list it explicitly here.
        "CoreBluetooth",
        "CoreFoundation",
        "libdispatch",
        "dispatch",
    ],
    "includes": [
        "cffi",
        "_cffi_backend",
        "pkg_resources",
    ],
    "excludes": [
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
        "pytest",
    ],
}

setup(
    app=APP,
    name="Moonlight",
    version=SETUP_VERSION,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
