#!/bin/bash
# Setup script for Moonlight lamp controller.
# Installs Python dependencies, configures Claude Code hooks, and sets up audio.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK_PATH="$SCRIPT_DIR/moonlight_hook.sh"
SETTINGS_FILE="$HOME/.claude/settings.json"

echo "=== Moonlight Setup ==="
echo ""

# 1. Install Python dependencies
echo "1. Installing Python dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt"
echo "   Done."
echo ""

# 2. Make hook executable
chmod +x "$HOOK_PATH"

# 3. Configure Claude Code hooks
echo "2. Configuring Claude Code hooks..."
echo "   Hook path: $HOOK_PATH"
echo ""

# Build the hooks config
HOOKS_JSON=$(cat <<ENDJSON
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "$HOOK_PATH idle"
      }
    ],
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "$HOOK_PATH working"
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "$HOOK_PATH idle"
      }
    ],
    "PreToolUse": [
      {
        "type": "command",
        "command": "$HOOK_PATH working"
      }
    ],
    "PostToolUse": [
      {
        "type": "command",
        "command": "$HOOK_PATH working"
      }
    ],
    "Notification": [
      {
        "type": "command",
        "command": "$HOOK_PATH input"
      }
    ],
    "SessionEnd": [
      {
        "type": "command",
        "command": "$HOOK_PATH off"
      }
    ]
  }
}
ENDJSON
)

# Merge hooks into existing settings
if [ -f "$SETTINGS_FILE" ]; then
    echo "   Merging hooks into existing settings.json..."
    python3 -c "
import json, sys

with open('$SETTINGS_FILE') as f:
    settings = json.load(f)

hooks = json.loads('''$HOOKS_JSON''')
settings['hooks'] = hooks['hooks']

with open('$SETTINGS_FILE', 'w') as f:
    json.dump(settings, f, indent=2)

print('   Hooks configured successfully.')
"
else
    echo "   Creating settings.json with hooks..."
    mkdir -p "$(dirname "$SETTINGS_FILE")"
    echo "$HOOKS_JSON" | python3 -c "
import json, sys
data = json.load(sys.stdin)
# Wrap with permissions
data['permissions'] = {}
with open('$SETTINGS_FILE', 'w') as f:
    json.dump(data, f, indent=2)
print('   Settings created.')
"
fi

echo ""

# 4. Check for BlackHole (for music mode)
echo "3. Checking audio setup for music mode..."
if system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole"; then
    echo "   BlackHole detected - music mode ready!"
else
    echo "   BlackHole not found. To enable music mode:"
    echo "     brew install blackhole-2ch"
    echo ""
    echo "   Then open Audio MIDI Setup and create a Multi-Output Device"
    echo "   combining your speakers + BlackHole 2ch."
    echo "   Set that Multi-Output Device as your system output."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start the menu bar app:"
echo "  python3 $SCRIPT_DIR/moonlight_app.py"
echo ""
echo "The app will:"
echo "  - Connect to your Moonside Halo lamp via Bluetooth"
echo "  - Show a 🌙 icon in the menu bar"
echo "  - Switch between Manual, Claude Code, and Music modes"
echo ""
