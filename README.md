# Moonlight

A macOS menu bar app that controls a Moonside Halo Bluetooth lamp. It gives
you quick access to presets, effects, and brightness from the status bar, and
adds two fun modes on top: a Claude Code session indicator and a real-time
music visualizer.

> Moonlight is an independent, unofficial controller. "Moonside" and
> "Moonside Halo" are trademarks of their respective owners; this project is
> not affiliated with or endorsed by them.

## Features

- **Menu bar control** — color presets, effects (rainbow, fire, gradients,
  twinkle, wave, pulsing), and brightness, all one click away
- **Schedule** — set daily on/off times with a configurable on-action (solid
  color, effect, or a whole mode)
- **Claude Code session lamp** — the lamp reflects what Claude Code is doing:
  thinking/working (animated), idle (amber), waiting for input (purple),
  session ended (off)
- **Music visualizer** — captures system audio via BlackHole and drives the
  lamp's built-in themes in real time, with bass/mid/treble driving hue and
  beat detection
- **Dock toggle** — runs as a menu bar accessory by default; show in the
  Dock with one click if you prefer
- **Multi-Mac handoff** — release the lamp from one Mac with a single click
  so another can pick it up, without quitting the app

## Install

1. Download the latest `Moonlight-<version>-arm64.zip` from
   [Releases](../../releases).
2. Unzip it and drag `Moonlight.app` into `/Applications`.
3. **Clear the quarantine flag** (unsigned app — see below):
   ```sh
   xattr -cr /Applications/Moonlight.app
   ```
4. Launch from Spotlight or `/Applications`.
5. On first launch, grant Bluetooth and (optionally) Microphone access when
   prompted.

### Why `xattr -cr`?

Moonlight is not code-signed or notarized yet, so macOS quarantines it on
download. Without this step you'll see a "Moonlight is damaged and can't be
opened" dialog, which is Gatekeeper's way of handling an unsigned app
delivered over the internet. `xattr -cr` strips the quarantine extended
attribute; after that, macOS will still ask once whether you want to open
it. This is a known limitation and will be resolved once the project moves
to a signed, notarized build.

## Using Moonlight on multiple Macs

The Moonside Halo, like most consumer BLE peripherals, only lets one
central (one Mac) hold the connection at a time. If Moonlight is running
on Mac A and you open it on Mac B, Mac B will see the lamp advertising
but won't be able to connect — the menu bar will show
**Status: Held by another device**.

To hand the lamp off without quitting:

1. On the Mac that currently has the lamp, click the Moonlight menu and
   choose **Release Lamp**. The status changes to **Released** and the
   icon goes to 🌜.
2. On the other Mac, Moonlight will pick the lamp up within a few seconds
   (or click **Reconnect Lamp** if you had released it there too).
3. To take the lamp back, click **Reconnect Lamp** on the first Mac — just
   make sure you've released it on the other one first.

Quitting Moonlight also releases the lamp, so the old "quit on Mac A,
launch on Mac B" flow still works. Release Lamp just saves you the
relaunch.

## Optional setup

### Music visualizer

The music mode needs a loopback audio device so the app can capture what's
playing through your speakers:

```sh
brew install blackhole-2ch
```

Then open *Audio MIDI Setup* and create a *Multi-Output Device* that
includes both your speakers and BlackHole 2ch. Set that Multi-Output Device
as your system output. The app will pick up BlackHole automatically when
you enable Music mode.

### Claude Code session lamp

If you have [Claude Code](https://claude.com/claude-code) installed, click
**Claude Code Hooks** in the Moonlight menu to toggle it on — a checkmark
appears when the hooks are active. Moonlight merges a handful of inline
commands into `~/.claude/settings.json` (preserving any hooks you already
have) and the lamp starts tracking your session the next time you open
Claude Code. Click the item again to remove the hooks.

No shell commands, no repo clone, no script on disk.

## Development

Requirements: Apple Silicon Mac, Python 3.12+, a Moonside Halo lamp.

```sh
git clone https://github.com/lukebrooker/moonlight.git
cd moonlight
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python moonlight_app.py
```

### Building a release bundle

```sh
MOONLIGHT_VERSION=1.0.0 ./build_release.sh
```

This produces `dist/Moonlight.app` and `dist/Moonlight-1.0.0-arm64.zip`.
GitHub Actions runs the same script on tag pushes (see
`.github/workflows/release.yml`) and attaches the zip to a GitHub Release.

## Why it's not signed

Signing and notarization require an Apple Developer Program membership.
Moonlight is a side project, so for now it ships unsigned and you run
`xattr -cr` once at install time. Getting it signed is on the roadmap.

## License

[MIT](LICENSE)
