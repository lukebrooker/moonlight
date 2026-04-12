#!/bin/bash
# Launch the Moonlight menu bar app (.app bundle built with py2app)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill any existing instance
pkill -f "moonlight_app.py" 2>/dev/null
pkill -f "Moonlight.app/Contents/MacOS/Moonlight" 2>/dev/null
sleep 0.3

APP_PATH="$SCRIPT_DIR/dist/Moonlight.app"
if [ ! -d "$APP_PATH" ]; then
    echo "Moonlight.app not built yet. Run: ./build_release.sh"
    exit 1
fi

open "$APP_PATH"
echo "Moonlight launched. Check the menu bar for 🌙"
