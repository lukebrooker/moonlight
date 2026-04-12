#!/bin/bash
# Build a self-contained Moonlight.app and zip it for release.
#
# Usage:
#   MOONLIGHT_VERSION=1.0.0 ./build_release.sh
#
# If MOONLIGHT_VERSION is unset, defaults to "0.0.0-dev".
# Works both locally (uses .venv/bin/python if present) and in CI
# (falls back to python3 on PATH).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Resolve version: strip a leading "v" so tags like v1.2.3 produce 1.2.3
RAW_VERSION="${MOONLIGHT_VERSION:-0.0.0-dev}"
VERSION="${RAW_VERSION#v}"
export MOONLIGHT_VERSION="$VERSION"

echo "=== Building Moonlight $VERSION ==="

# Pick Python: prefer venv, fall back to system python3
if [ -x ".venv/bin/python" ]; then
    PYTHON="$(pwd)/.venv/bin/python"
    echo "Using virtualenv: $(pwd)/.venv"
else
    PYTHON="$(command -v python3)"
    echo "Using system Python: $PYTHON"
fi

# 1. Clean previous build output
echo ""
echo "--- Cleaning previous build artifacts ---"
rm -rf build dist Moonlight.iconset icon.icns

# 2. Install dependencies (py2app included). Use `python -m pip` so we never
# rely on a pip wrapper script's hardcoded shebang — important for both CI
# and locally if the venv has ever been moved.
echo ""
echo "--- Installing dependencies ---"
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m pip install 'py2app>=0.28.10'

# 3. Generate icon
echo ""
echo "--- Generating icon ---"
"$PYTHON" build_icon.py

# 4. Build the .app
echo ""
echo "--- Running py2app ---"
"$PYTHON" setup.py py2app

APP_PATH="dist/Moonlight.app"
if [ ! -d "$APP_PATH" ]; then
    echo "ERROR: $APP_PATH was not produced" >&2
    exit 1
fi

# 5. Sanity check Info.plist
echo ""
echo "--- Verifying Info.plist ---"
PLIST="$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Print :CFBundleName" "$PLIST"
/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$PLIST"
/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$PLIST"

# 6. Zip the bundle with ditto (preserves bundle metadata; zip does not)
ZIP_NAME="Moonlight-${VERSION}-arm64.zip"
ZIP_PATH="dist/${ZIP_NAME}"

echo ""
echo "--- Packaging $ZIP_NAME ---"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

SIZE=$(du -h "$ZIP_PATH" | awk '{print $1}')
echo ""
echo "=== Done ==="
echo "Bundle:  $SCRIPT_DIR/$APP_PATH"
echo "Zip:     $SCRIPT_DIR/$ZIP_PATH ($SIZE)"
