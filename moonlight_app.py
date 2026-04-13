"""Moonlight - Mac menu bar app for controlling Moonside Halo lamp.

Modes:
  - Manual: pick colors, effects, brightness from the menu
  - Claude Code: lamp reacts to Claude Code session events via hooks
  - Music: lamp visualizes system audio (requires BlackHole)
"""

import datetime
import json
import logging
import os
import shutil
import sys
import threading
import time

import AppKit
import rumps

from moonlight_ble import MoonlightBLE
from moonlight_music import MusicVisualizer

# NSApplication activation policies
NS_ACTIVATION_POLICY_REGULAR = 0    # Dock icon + Cmd+Tab
NS_ACTIVATION_POLICY_ACCESSORY = 1  # Menu bar only

log = logging.getLogger("moonlight")

STATE_FILE = "/tmp/moonlight_state"
CONFIG_FILE = os.path.expanduser("~/.config/moonlight/config.json")
LEGACY_CONFIG_FILE = os.path.expanduser("~/.config/moonside/config.json")

# Claude Code hook integration.
#
# The lamp reacts to Claude Code sessions by watching STATE_FILE. A hook is
# just a shell command that writes a word to that file — so we can install
# the hooks as inline `echo` commands instead of shipping a script that
# requires a repo checkout.
CLAUDE_SETTINGS_FILE = os.path.expanduser("~/.claude/settings.json")

# (event name in settings.json, state word to write)
CLAUDE_HOOK_EVENTS = [
    ("SessionStart", "idle"),
    ("UserPromptSubmit", "working"),
    ("Stop", "idle"),
    ("PreToolUse", "working"),
    ("PostToolUse", "working"),
    ("Notification", "input"),
    ("SessionEnd", "off"),
]

# Substrings that identify a hook as ours so we can add/remove it without
# stomping on unrelated hooks the user has configured. Includes the old
# script-based hook paths so upgrades from setup.sh installs get cleaned up.
CLAUDE_HOOK_MARKERS = (
    "/tmp/moonlight_state",
    "moonlight_hook.sh",
    "moonside_hook.sh",
)

# Claude Code state -> lamp command mapping
CLAUDE_STATES = {
    "working": {"type": "theme", "theme": "BEAT2", "colors": [(255, 255, 255), (0, 0, 140)]},
    "idle": {"type": "color", "r": 255, "g": 180, "b": 50},
    "input": {"type": "color", "r": 200, "g": 0, "b": 255},
    "off": {"type": "off"},
}

# Preset colors for manual mode
COLOR_PRESETS = {
    "Warm White": (255, 200, 150),
    "Cool White": (200, 220, 255),
    "Sunset": (255, 100, 30),
    "Ocean": (0, 100, 255),
    "Forest": (30, 200, 60),
    "Purple Haze": (160, 50, 255),
    "Coral": (255, 80, 100),
    "Cyan": (0, 220, 220),
}

EFFECTS = {
    "Rainbow": ("RAINBOW3", [(0,)]),
    "Fire": ("FIRE2", [(255, 80, 0)]),
    "Lava": ("LAVA1", [(255, 50, 0), (200, 0, 100)]),
    "Gradient Blue-Purple": ("GRADIENT1", [(0, 100, 255), (200, 0, 255)]),
    "Gradient Sunset": ("GRADIENT1", [(255, 60, 0), (255, 200, 50)]),
    "Twinkle": ("TWINKLE1", [(255, 255, 255)]),
    "Wave": ("WAVE1", [(0, 150, 255), (255, 50, 200)]),
    "Pulsing": ("PULSING1", [(200, 0, 255)]),
}


class MoonlightApp(rumps.App):
    def __init__(self):
        super().__init__("Moonlight", title="🌑", quit_button=None)

        self.ble = MoonlightBLE()
        self.music = MusicVisualizer(self.ble)
        self._mode = "manual"  # manual, claude, music
        self._claude_thread: threading.Thread | None = None
        self._brightness = 80

        # Schedule
        self._schedule_on: str | None = None   # "HH:MM"
        self._schedule_off: str | None = None  # "HH:MM"
        # On action: what the lamp does when the schedule turns it on.
        # Examples:
        #   {"type": "color", "name": "Warm White", "r": 255, "g": 200, "b": 150}
        #   {"type": "effect", "name": "Rainbow"}
        #   {"type": "mode", "mode": "claude"}
        #   {"type": "mode", "mode": "music"}
        self._schedule_action: dict | None = None
        self._schedule_timer: rumps.Timer | None = None
        self._last_on_triggered = ""
        self._last_off_triggered = ""

        # Dock visibility (loaded from config in _load_schedule)
        self._show_in_dock = False

        # Auto-heal settings.json if we find hooks installed by the
        # broken v1.0.2 menu flow. Runs before _build_menu so the
        # checkmark state reflects the post-repair reality.
        self._repair_claude_hooks_if_needed()

        self._build_menu()
        self.ble.start(on_connection_change=self._on_ble_connection)

    def _build_menu(self):
        claude_hooks_item = rumps.MenuItem(
            "Claude Code Hooks", callback=self._toggle_claude_hooks
        )
        claude_hooks_item.state = 1 if self._claude_hooks_installed() else 0

        self.menu = [
            rumps.MenuItem("Status: Connecting...", callback=None),
            None,  # separator
            rumps.MenuItem("Mode"),
            None,
            rumps.MenuItem("Colors"),
            rumps.MenuItem("Effects"),
            rumps.MenuItem("Brightness"),
            None,
            rumps.MenuItem("Schedule"),
            None,
            rumps.MenuItem("Turn On", callback=self._on_turn_on),
            rumps.MenuItem("Turn Off", callback=self._on_turn_off),
            None,
            rumps.MenuItem("Release Lamp", callback=self._toggle_release),
            None,
            claude_hooks_item,
            None,
            rumps.MenuItem("Show in Dock", callback=self._toggle_dock),
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]

        # Mode submenu
        mode_menu = self.menu["Mode"]
        mode_menu.add(rumps.MenuItem("Manual", callback=self._set_mode_manual))
        mode_menu.add(rumps.MenuItem("Claude Code", callback=self._set_mode_claude))
        mode_menu.add(rumps.MenuItem("Music Visualizer", callback=self._set_mode_music))
        mode_menu["Manual"].state = 1  # checkmark

        # Color presets submenu
        colors_menu = self.menu["Colors"]
        for name, (r, g, b) in COLOR_PRESETS.items():
            colors_menu.add(rumps.MenuItem(name, callback=self._make_color_callback(r, g, b)))

        # Effects submenu
        effects_menu = self.menu["Effects"]
        for name in EFFECTS:
            effects_menu.add(rumps.MenuItem(name, callback=self._make_effect_callback(name)))

        # Brightness submenu
        bright_menu = self.menu["Brightness"]
        for pct in [100, 80, 60, 40, 20]:
            val = int(pct * 1.2)  # scale 0-100% to 0-120
            item = rumps.MenuItem(f"{pct}%", callback=self._make_brightness_callback(val))
            if pct == 80:
                item.state = 1
            bright_menu.add(item)

        # Schedule submenu
        schedule_menu = self.menu["Schedule"]
        schedule_menu.add(rumps.MenuItem("Set Turn On Time...", callback=self._set_schedule_on))
        schedule_menu.add(rumps.MenuItem("Set Turn Off Time...", callback=self._set_schedule_off))
        schedule_menu.add(rumps.MenuItem("On Action"))
        schedule_menu.add(None)  # separator
        schedule_menu.add(rumps.MenuItem("On: Not set", callback=None))
        schedule_menu.add(rumps.MenuItem("Off: Not set", callback=None))
        schedule_menu.add(rumps.MenuItem("Action: Warm White", callback=None))
        schedule_menu.add(None)
        schedule_menu.add(rumps.MenuItem("Clear Schedule", callback=self._clear_schedule))

        # On Action submenu: all the same things you can set manually
        action_menu = schedule_menu["On Action"]

        # Manual > Colors
        colors_submenu = rumps.MenuItem("Color")
        for name, (r, g, b) in COLOR_PRESETS.items():
            colors_submenu.add(
                rumps.MenuItem(name, callback=self._make_schedule_color_callback(name, r, g, b))
            )
        action_menu.add(colors_submenu)

        # Manual > Effects
        effects_submenu = rumps.MenuItem("Effect")
        for name in EFFECTS:
            effects_submenu.add(
                rumps.MenuItem(name, callback=self._make_schedule_effect_callback(name))
            )
        action_menu.add(effects_submenu)

        action_menu.add(None)
        action_menu.add(rumps.MenuItem("Claude Code Mode", callback=self._set_schedule_action_claude))
        action_menu.add(rumps.MenuItem("Music Visualizer Mode", callback=self._set_schedule_action_music))

        # Default action (may be overridden by _load_schedule)
        if self._schedule_action is None:
            self._schedule_action = {"type": "color", "name": "Warm White", "r": 255, "g": 200, "b": 150}

        # Load saved schedule (overrides default action if present)
        self._load_schedule()
        self._update_schedule_display()
        self._apply_dock_visibility()

    # -- Connection callbacks --

    def _on_ble_connection(self, connected: bool):
        """Called from BLE thread when connection state changes."""
        status_key = self._find_status_key()
        if connected:
            self.title = "🌙"
            if status_key:
                self.menu[status_key].title = "Status: Connected"
            # Set initial brightness
            self.ble.send_brightness(self._brightness)
        else:
            # Three disconnect flavors we want to distinguish in the menu:
            # released (we intentionally let go), held-by-other (another Mac
            # owns the lamp), plain disconnected (lamp off / out of range).
            if self.ble.released:
                self.title = "🌜"
                new_title = "Status: Released"
            elif self.ble.held_by_other:
                self.title = "🌒"
                new_title = "Status: Held by another device"
            else:
                self.title = "🌑"
                new_title = "Status: Disconnected"
            if status_key:
                self.menu[status_key].title = new_title

    def _find_status_key(self) -> str | None:
        for key in self.menu:
            if isinstance(key, str) and key.startswith("Status:"):
                return key
        return None

    # -- Mode switching --

    def _clear_mode_checks(self):
        mode_menu = self.menu["Mode"]
        mode_menu["Manual"].state = 0
        mode_menu["Claude Code"].state = 0
        mode_menu["Music Visualizer"].state = 0

    def _set_mode_manual(self, sender):
        self._stop_active_mode()
        self._mode = "manual"
        self._clear_mode_checks()
        sender.state = 1

    def _set_mode_claude(self, sender):
        self._stop_active_mode()
        self._mode = "claude"
        self._clear_mode_checks()
        sender.state = 1
        self._start_claude_watcher()

    def _set_mode_music(self, sender):
        self._stop_active_mode()
        self._mode = "music"
        self._clear_mode_checks()
        sender.state = 1
        self._start_music()

    def _stop_active_mode(self):
        if self._mode == "claude":
            self._stop_claude_watcher()
        elif self._mode == "music":
            self.music.stop()

    # -- Manual controls --

    def _make_color_callback(self, r, g, b):
        def callback(sender):
            if self._mode != "manual":
                self._set_mode_manual(self.menu["Mode"]["Manual"])
            self.ble.send_color(r, g, b)
        return callback

    def _make_effect_callback(self, name):
        def callback(sender):
            if self._mode != "manual":
                self._set_mode_manual(self.menu["Mode"]["Manual"])
            theme, colors = EFFECTS[name]
            if theme.startswith("RAINBOW"):
                # Rainbow takes a speed param, not RGB
                self.ble.send(f"THEME.{theme}.0,")
            else:
                self.ble.send_theme(theme, colors)
        return callback

    def _make_brightness_callback(self, value):
        def callback(sender):
            self._brightness = value
            self.ble.send_brightness(value)
            # Update checkmarks
            for item in self.menu["Brightness"].values():
                item.state = 0
            sender.state = 1
        return callback

    def _on_turn_on(self, sender):
        self.ble.send_on()

    def _on_turn_off(self, sender):
        self.ble.send_off()

    def _toggle_release(self, sender):
        """Hand the lamp off to another Mac without quitting.

        Disconnects our BLE client and pauses the reconnect loop so another
        central (another Mac) can grab the lamp. Click again to resume.
        """
        if self.ble.released:
            self.ble.resume()
            sender.title = "Release Lamp"
        else:
            self.ble.release()
            sender.title = "Reconnect Lamp"
            # Update status immediately — the BLE callback will fire too,
            # but only after the current connection tears down.
            self.title = "🌜"
            status_key = self._find_status_key()
            if status_key:
                self.menu[status_key].title = "Status: Released"

    # -- Claude Code hook installation --
    #
    # The hooks are inline `echo` commands rather than a shell script, so
    # the app is self-contained: install Moonlight.app, click the menu
    # item, done. No repo clone, no external script on disk.

    def _claude_hook_command(self, state: str) -> str:
        return f"echo -n {state} > {STATE_FILE}"

    def _is_moonlight_command(self, cmd_dict) -> bool:
        """True if a {type, command} dict is one of our hook commands."""
        if not isinstance(cmd_dict, dict):
            return False
        cmd = cmd_dict.get("command") or ""
        return any(marker in cmd for marker in CLAUDE_HOOK_MARKERS)

    def _is_moonlight_hook_entry(self, entry) -> bool:
        """True if an event-level entry in settings.json is one of ours.

        Recognises both the correct Claude Code schema —
        ``{"hooks": [{type, command}]}`` — and the broken v1.0.2 schema
        where we wrote command dicts directly at the event level. The
        latter is what we clean up when upgrading.
        """
        if not isinstance(entry, dict):
            return False
        # Broken v1.0.2 shape: bare command dict at the event level
        if "command" in entry and "hooks" not in entry:
            return self._is_moonlight_command(entry)
        # Correct shape: matcher group with a nested hooks list
        inner = entry.get("hooks")
        if isinstance(inner, list):
            return any(self._is_moonlight_command(h) for h in inner)
        return False

    def _filter_moonlight_entries(self, entries: list) -> list:
        """Return a copy of ``entries`` with all Moonlight hooks stripped.

        Preserves any non-Moonlight entries untouched, including other
        matcher groups the user has configured.
        """
        cleaned = []
        for entry in entries:
            if not isinstance(entry, dict):
                cleaned.append(entry)
                continue
            # Broken v1.0.2 shape — drop wholesale if it's ours
            if "command" in entry and "hooks" not in entry:
                if not self._is_moonlight_command(entry):
                    cleaned.append(entry)
                continue
            # Correct shape — filter the inner hooks list
            inner = entry.get("hooks")
            if not isinstance(inner, list):
                cleaned.append(entry)
                continue
            new_inner = [h for h in inner if not self._is_moonlight_command(h)]
            if new_inner:
                new_entry = dict(entry)
                new_entry["hooks"] = new_inner
                cleaned.append(new_entry)
            # else: the entry contained only our hooks — drop it
        return cleaned

    def _claude_hooks_installed(self) -> bool:
        """Best-effort check whether our hooks are present in settings.json."""
        settings = self._read_claude_settings()
        hooks = settings.get("hooks") or {}
        if not isinstance(hooks, dict):
            return False
        for entries in hooks.values():
            if isinstance(entries, list) and any(
                self._is_moonlight_hook_entry(e) for e in entries
            ):
                return True
        return False

    def _read_claude_settings(self) -> dict:
        if not os.path.exists(CLAUDE_SETTINGS_FILE):
            return {}
        try:
            with open(CLAUDE_SETTINGS_FILE) as f:
                return json.load(f)
        except Exception:
            log.exception("Failed to read %s", CLAUDE_SETTINGS_FILE)
            return {}

    def _write_claude_settings(self, settings: dict):
        os.makedirs(os.path.dirname(CLAUDE_SETTINGS_FILE), exist_ok=True)
        with open(CLAUDE_SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)

    def _install_claude_hooks(self):
        """Merge Moonlight's hooks into settings.json, preserving others.

        Uses Claude Code's matcher-group schema: each event gets a group
        ``{"hooks": [{type, command}]}``. Matcher is omitted since we want
        the hook to run for every event occurrence.
        """
        settings = self._read_claude_settings()
        hooks = settings.get("hooks") or {}
        if not isinstance(hooks, dict):
            hooks = {}

        for event, state in CLAUDE_HOOK_EVENTS:
            existing = hooks.get(event) or []
            if not isinstance(existing, list):
                existing = []
            filtered = self._filter_moonlight_entries(existing)
            filtered.append({
                "hooks": [
                    {
                        "type": "command",
                        "command": self._claude_hook_command(state),
                    }
                ]
            })
            hooks[event] = filtered

        settings["hooks"] = hooks
        self._write_claude_settings(settings)

    def _uninstall_claude_hooks(self):
        """Remove Moonlight's hooks from settings.json, preserving others."""
        if not os.path.exists(CLAUDE_SETTINGS_FILE):
            return
        settings = self._read_claude_settings()
        hooks = settings.get("hooks") or {}
        if not isinstance(hooks, dict):
            return

        cleaned = {}
        for event, entries in hooks.items():
            if not isinstance(entries, list):
                cleaned[event] = entries
                continue
            filtered = self._filter_moonlight_entries(entries)
            if filtered:
                cleaned[event] = filtered

        if cleaned:
            settings["hooks"] = cleaned
        else:
            settings.pop("hooks", None)
        self._write_claude_settings(settings)

    def _repair_claude_hooks_if_needed(self):
        """Auto-repair hooks installed by the broken v1.0.2 menu install.

        v1.0.2 wrote event entries as bare ``{type, command}`` dicts,
        which Claude Code rejects as a schema error. Detect that shape
        on launch and quietly rewrite the hooks in the correct schema.
        No-op when settings.json is clean or doesn't exist.
        """
        if not os.path.exists(CLAUDE_SETTINGS_FILE):
            return
        settings = self._read_claude_settings()
        hooks = settings.get("hooks") or {}
        if not isinstance(hooks, dict):
            return
        needs_repair = False
        for entries in hooks.values():
            if not isinstance(entries, list):
                continue
            for e in entries:
                if (
                    isinstance(e, dict)
                    and "command" in e
                    and "hooks" not in e
                    and self._is_moonlight_command(e)
                ):
                    needs_repair = True
                    break
            if needs_repair:
                break
        if needs_repair:
            log.info("Repairing Moonlight's Claude Code hooks in settings.json")
            try:
                self._install_claude_hooks()
            except Exception:
                log.exception("Failed to repair Claude Code hooks")

    def _toggle_claude_hooks(self, sender):
        try:
            if self._claude_hooks_installed():
                self._uninstall_claude_hooks()
                sender.state = 0
                rumps.alert(
                    title="Claude Code hooks removed",
                    message=(
                        "Moonlight's hooks have been removed from "
                        "~/.claude/settings.json. Open a new Claude Code "
                        "session for the change to take effect."
                    ),
                )
            else:
                self._install_claude_hooks()
                sender.state = 1
                rumps.alert(
                    title="Claude Code hooks installed",
                    message=(
                        "Moonlight is now wired into Claude Code.\n\n"
                        "Switch to Claude Code mode from the Mode menu, "
                        "then open a new Claude Code session — the lamp "
                        "will start reacting to session events."
                    ),
                )
        except Exception:
            log.exception("Failed to toggle Claude Code hooks")
            rumps.alert(
                title="Couldn't update hooks",
                message=(
                    "Moonlight couldn't update ~/.claude/settings.json. "
                    "Check Console.app for details."
                ),
            )

    # -- Claude Code mode --

    def _start_claude_watcher(self):
        """Watch the state file for Claude Code events."""
        self._claude_running = True
        self._claude_thread = threading.Thread(target=self._claude_loop, daemon=True)
        self._claude_thread.start()
        # Set initial idle state
        self._apply_claude_state("idle")

    def _stop_claude_watcher(self):
        self._claude_running = False
        if self._claude_thread:
            self._claude_thread.join(timeout=2)
            self._claude_thread = None

    def _claude_loop(self):
        """Poll state file for changes."""
        last_state = None
        while self._claude_running and self._mode == "claude":
            try:
                if os.path.exists(STATE_FILE):
                    with open(STATE_FILE) as f:
                        state = f.read().strip()
                    if state != last_state and state in CLAUDE_STATES:
                        self._apply_claude_state(state)
                        last_state = state
            except Exception:
                log.exception("Error reading state file")
            time.sleep(0.2)

    def _apply_claude_state(self, state: str):
        """Send lamp commands for a Claude Code state."""
        cfg = CLAUDE_STATES.get(state)
        if not cfg:
            return

        if cfg["type"] == "off":
            self.ble.send_off()
        elif cfg["type"] == "color":
            self.ble.send_color(cfg["r"], cfg["g"], cfg["b"])
        elif cfg["type"] == "theme":
            self.ble.send_theme(cfg["theme"], cfg["colors"])

    # -- Music mode --

    def _start_music(self):
        """Start music visualizer with BlackHole device."""
        device_idx = self.music.find_blackhole_device()
        if device_idx is None:
            rumps.alert(
                title="BlackHole Not Found",
                message=(
                    "Music mode requires BlackHole 2ch to capture system audio.\n\n"
                    "Install it with:\n"
                    "  brew install blackhole-2ch\n\n"
                    "Then create a Multi-Output Device in Audio MIDI Setup "
                    "that includes both your speakers and BlackHole 2ch."
                ),
            )
            self._set_mode_manual(self.menu["Mode"]["Manual"])
            return
        self.music.start(device_index=device_idx)

    # -- Schedule mode --

    def _set_schedule_on(self, sender):
        """Prompt user to set auto-on time."""
        response = rumps.Window(
            title="Turn On Time",
            message="Enter time to turn on (24h format):",
            default_text=self._schedule_on or "08:00",
            ok="Set",
            cancel="Cancel",
            dimensions=(200, 24),
        ).run()
        if response.clicked:
            time_str = response.text.strip()
            if self._validate_time(time_str):
                self._schedule_on = time_str
                self._update_schedule_display()
                self._save_schedule()
                self._start_schedule_watcher()
            else:
                rumps.alert("Invalid time", "Please use HH:MM format (e.g. 08:00, 17:30)")

    def _set_schedule_off(self, sender):
        """Prompt user to set auto-off time."""
        response = rumps.Window(
            title="Turn Off Time",
            message="Enter time to turn off (24h format):",
            default_text=self._schedule_off or "23:00",
            ok="Set",
            cancel="Cancel",
            dimensions=(200, 24),
        ).run()
        if response.clicked:
            time_str = response.text.strip()
            if self._validate_time(time_str):
                self._schedule_off = time_str
                self._update_schedule_display()
                self._save_schedule()
                self._start_schedule_watcher()
            else:
                rumps.alert("Invalid time", "Please use HH:MM format (e.g. 08:00, 17:30)")

    def _clear_schedule(self, sender):
        """Clear all scheduled times."""
        self._schedule_on = None
        self._schedule_off = None
        self._stop_schedule_watcher()
        self._update_schedule_display()
        self._save_schedule()

    # -- Schedule on-action setters --

    def _make_schedule_color_callback(self, name, r, g, b):
        def callback(sender):
            self._schedule_action = {
                "type": "color", "name": name, "r": r, "g": g, "b": b,
            }
            self._update_schedule_display()
            self._save_schedule()
        return callback

    def _make_schedule_effect_callback(self, name):
        def callback(sender):
            self._schedule_action = {"type": "effect", "name": name}
            self._update_schedule_display()
            self._save_schedule()
        return callback

    def _set_schedule_action_claude(self, sender):
        self._schedule_action = {"type": "mode", "mode": "claude", "name": "Claude Code"}
        self._update_schedule_display()
        self._save_schedule()

    def _set_schedule_action_music(self, sender):
        self._schedule_action = {"type": "mode", "mode": "music", "name": "Music Visualizer"}
        self._update_schedule_display()
        self._save_schedule()

    def _action_label(self) -> str:
        """Return a short label describing the current on-action."""
        if not self._schedule_action:
            return "Not set"
        return self._schedule_action.get("name", self._schedule_action.get("type", "Unknown"))

    def _validate_time(self, time_str: str) -> bool:
        """Check if string is valid HH:MM format."""
        try:
            parts = time_str.split(":")
            if len(parts) != 2:
                return False
            h, m = int(parts[0]), int(parts[1])
            return 0 <= h <= 23 and 0 <= m <= 59
        except ValueError:
            return False

    def _update_schedule_display(self):
        """Update the schedule menu items to show current times and action."""
        schedule_menu = self.menu["Schedule"]
        on_text = f"On: {self._schedule_on}" if self._schedule_on else "On: Not set"
        off_text = f"Off: {self._schedule_off}" if self._schedule_off else "Off: Not set"
        action_text = f"Action: {self._action_label()}"

        # Find and update the display items
        for key in list(schedule_menu.keys()):
            if key.startswith("On:"):
                schedule_menu[key].title = on_text
            elif key.startswith("Off:"):
                schedule_menu[key].title = off_text
            elif key.startswith("Action:"):
                schedule_menu[key].title = action_text

    def _apply_schedule_action(self):
        """Apply the configured on-action (color, effect, or mode)."""
        action = self._schedule_action
        if not action:
            self.ble.send_on()
            return

        kind = action.get("type")
        if kind == "color":
            # Switch to manual mode and set the color
            if self._mode != "manual":
                self._set_mode_manual(self.menu["Mode"]["Manual"])
            self.ble.send_color(action["r"], action["g"], action["b"])
        elif kind == "effect":
            if self._mode != "manual":
                self._set_mode_manual(self.menu["Mode"]["Manual"])
            name = action["name"]
            theme, colors = EFFECTS[name]
            if theme.startswith("RAINBOW"):
                self.ble.send(f"THEME.{theme}.0,")
            else:
                self.ble.send_theme(theme, colors)
        elif kind == "mode":
            mode = action.get("mode")
            if mode == "claude":
                self._set_mode_claude(self.menu["Mode"]["Claude Code"])
            elif mode == "music":
                self._set_mode_music(self.menu["Mode"]["Music Visualizer"])

    def _start_schedule_watcher(self):
        """Start a rumps.Timer to check the schedule on the main thread."""
        if self._schedule_timer is not None:
            return
        if not self._schedule_on and not self._schedule_off:
            return
        # Check every 20 seconds; this runs on the main thread so it's safe
        # to call menu/mode operations directly from _check_schedule.
        self._schedule_timer = rumps.Timer(self._check_schedule, 20)
        self._schedule_timer.start()

    def _stop_schedule_watcher(self):
        if self._schedule_timer is not None:
            try:
                self._schedule_timer.stop()
            except Exception:
                pass
            self._schedule_timer = None

    def _check_schedule(self, _sender=None):
        """Called by rumps.Timer on the main thread. Safe to update UI/mode."""
        try:
            now = datetime.datetime.now().strftime("%H:%M")

            if self._schedule_on and now == self._schedule_on and now != self._last_on_triggered:
                log.info(f"Schedule: turning on at {now} ({self._action_label()})")
                self.ble.send_on()
                self._apply_schedule_action()
                self._last_on_triggered = now

            if self._schedule_off and now == self._schedule_off and now != self._last_off_triggered:
                log.info(f"Schedule: turning off at {now}")
                self._stop_active_mode()
                self._set_mode_manual(self.menu["Mode"]["Manual"])
                self.ble.send_off()
                self._last_off_triggered = now
        except Exception:
            log.exception("Schedule check failed")

    def _save_schedule(self):
        """Persist schedule to config file."""
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    config = json.load(f)
            except Exception:
                pass
        config["schedule_on"] = self._schedule_on
        config["schedule_off"] = self._schedule_off
        config["schedule_action"] = self._schedule_action
        config["show_in_dock"] = self._show_in_dock
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

    def _load_schedule(self):
        """Load saved schedule from config file."""
        # Migration: if we don't have a moonlight config yet but the old
        # moonside config exists, copy it over so the user keeps their
        # schedule/action/dock settings across the rename.
        if not os.path.exists(CONFIG_FILE) and os.path.exists(LEGACY_CONFIG_FILE):
            try:
                os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
                shutil.copy2(LEGACY_CONFIG_FILE, CONFIG_FILE)
                log.info(f"Migrated config from {LEGACY_CONFIG_FILE} to {CONFIG_FILE}")
            except Exception:
                log.exception("Failed to migrate legacy moonside config")

        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
            self._schedule_on = config.get("schedule_on")
            self._schedule_off = config.get("schedule_off")
            saved_action = config.get("schedule_action")
            if saved_action:
                self._schedule_action = saved_action
            self._show_in_dock = bool(config.get("show_in_dock", False))
            self._update_schedule_display()
            self._apply_dock_visibility()
            self._start_schedule_watcher()
        except Exception:
            log.exception("Failed to load schedule config")

    # -- Dock visibility --

    def _apply_dock_visibility(self):
        """Apply the current _show_in_dock setting to the running app."""
        policy = (
            NS_ACTIVATION_POLICY_REGULAR
            if self._show_in_dock
            else NS_ACTIVATION_POLICY_ACCESSORY
        )
        try:
            AppKit.NSApp.setActivationPolicy_(policy)
        except Exception:
            log.exception("Failed to set activation policy")
        # Update checkmark
        try:
            self.menu["Show in Dock"].state = 1 if self._show_in_dock else 0
        except Exception:
            pass

    def _toggle_dock(self, sender):
        self._show_in_dock = not self._show_in_dock
        self._apply_dock_visibility()
        self._save_schedule()

    # -- Quit --

    def _on_quit(self, sender):
        self._stop_active_mode()
        self._stop_schedule_watcher()
        self.ble.send_off()
        time.sleep(0.3)
        self.ble.stop()
        rumps.quit_application()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    app = MoonlightApp()
    app.run()


if __name__ == "__main__":
    main()
