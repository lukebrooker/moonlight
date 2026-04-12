#!/bin/bash
# Claude Code hook for Moonlight lamp controller.
# Writes the desired lamp state to /tmp/moonlight_state.
# The Moonlight menu bar app watches this file and sends BLE commands.
#
# Usage: moonlight_hook.sh <state>
# States: working, idle, input, off

STATE_FILE="/tmp/moonlight_state"
STATE="${1:-idle}"

echo -n "$STATE" > "$STATE_FILE"

# Always exit 0 so we never block Claude Code
exit 0
