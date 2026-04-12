"""Music visualizer for Moonside Halo lamps.

Captures system audio (via BlackHole) and maps frequency bands to animated
lamp effects. Uses the lamp's built-in themes (BEAT, FIRE, GRADIENT, WAVE, etc.)
with colors derived from real-time audio analysis.
"""

import logging
import threading
import time

import numpy as np

log = logging.getLogger("moonlight.music")

# Audio settings
SAMPLE_RATE = 44100
BLOCK_SIZE = 2048  # ~46ms at 44.1kHz

# How often to send theme/color updates
THEME_INTERVAL = 0.8  # Switch themes less often to let animations play
COLOR_INTERVAL = 0.08  # Solid color updates can be faster

# Frequency band ranges (Hz)
BASS_RANGE = (20, 250)
MID_RANGE = (250, 2000)
HIGH_RANGE = (2000, 16000)

# Smoothing
SMOOTHING = 0.25
ENERGY_SMOOTHING = 0.4

# Energy thresholds for mode switching
HIGH_ENERGY_THRESHOLD = 0.6
MID_ENERGY_THRESHOLD = 0.3


class MusicVisualizer:
    """Captures audio and drives lamp effects from frequency analysis."""

    def __init__(self, ble, device_name: str | None = None):
        self.ble = ble
        self.device_name = device_name
        self._running = False
        self._thread: threading.Thread | None = None
        self._available_devices: list[dict] = []

        # Smoothed values
        self._smooth_bass = 0.0
        self._smooth_mids = 0.0
        self._smooth_highs = 0.0
        self._smooth_energy = 0.0

        # Beat detection
        self._energy_history: list[float] = []
        self._last_beat_time = 0.0
        self._beat_count = 0

        # Current animation state
        self._current_mode = ""
        self._last_theme_time = 0.0
        self._last_color_time = 0.0

    def list_devices(self) -> list[dict]:
        """List available audio input devices."""
        import sounddevice as sd
        devices = sd.query_devices()
        inputs = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                inputs.append({"index": i, "name": d["name"], "channels": d["max_input_channels"]})
        self._available_devices = inputs
        return inputs

    def find_blackhole_device(self) -> int | None:
        """Find the BlackHole audio device index."""
        devices = self.list_devices()
        for d in devices:
            if "BlackHole" in d["name"]:
                return d["index"]
        return None

    def start(self, device_index: int | None = None):
        """Start the music visualizer."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, args=(device_index,), daemon=True
        )
        self._thread.start()

    def stop(self):
        """Stop the music visualizer."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self, device_index: int | None):
        """Main capture + analysis loop."""
        import sounddevice as sd

        try:
            log.info(f"Starting music visualizer (device={device_index})")
            self.ble.send_on()

            with sd.InputStream(
                device=device_index,
                channels=1,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype="float32",
            ) as stream:
                while self._running:
                    data, overflowed = stream.read(BLOCK_SIZE)
                    if overflowed:
                        log.debug("Audio buffer overflow")

                    self._process_audio(data[:, 0])

        except Exception:
            log.exception("Music visualizer error")
        finally:
            self._running = False

    def _process_audio(self, samples: np.ndarray):
        """Analyze audio and send appropriate lamp commands."""
        now = time.monotonic()

        # FFT analysis
        fft = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        freqs = np.fft.rfftfreq(len(samples), 1.0 / SAMPLE_RATE)

        # Raw band energies
        bass_raw = self._band_energy(fft, freqs, *BASS_RANGE)
        mids_raw = self._band_energy(fft, freqs, *MID_RANGE)
        highs_raw = self._band_energy(fft, freqs, *HIGH_RANGE)

        # Normalize
        bass = min(1.0, bass_raw * 8.0)
        mids = min(1.0, mids_raw * 12.0)
        highs = min(1.0, highs_raw * 20.0)

        # Smooth
        self._smooth_bass = self._smooth_bass * ENERGY_SMOOTHING + bass * (1 - ENERGY_SMOOTHING)
        self._smooth_mids = self._smooth_mids * ENERGY_SMOOTHING + mids * (1 - ENERGY_SMOOTHING)
        self._smooth_highs = self._smooth_highs * ENERGY_SMOOTHING + highs * (1 - ENERGY_SMOOTHING)

        # Overall energy
        amplitude = min(1.0, np.sqrt(np.mean(samples ** 2)) * 10.0)
        self._smooth_energy = self._smooth_energy * ENERGY_SMOOTHING + amplitude * (1 - ENERGY_SMOOTHING)

        # Beat detection (bass spike above recent average)
        self._energy_history.append(bass)
        if len(self._energy_history) > 50:
            self._energy_history.pop(0)
        avg_energy = np.mean(self._energy_history) if self._energy_history else 0
        is_beat = bass > avg_energy * 1.8 and bass > 0.3 and (now - self._last_beat_time) > 0.2
        if is_beat:
            self._last_beat_time = now
            self._beat_count += 1

        # Derive two colors from the current audio
        color1 = self._energy_to_color(self._smooth_bass, self._smooth_mids, self._smooth_highs)
        color2 = self._energy_to_accent(self._smooth_bass, self._smooth_mids, self._smooth_highs)

        # Pick mode based on energy characteristics
        if self._smooth_energy < 0.05:
            # Silence / very quiet - gentle pulsing
            mode = "quiet"
        elif self._smooth_bass > HIGH_ENERGY_THRESHOLD and self._smooth_bass > self._smooth_mids:
            # Heavy bass - fire or beat effect
            mode = "bass_heavy"
        elif self._smooth_highs > MID_ENERGY_THRESHOLD and self._smooth_highs > self._smooth_bass:
            # Treble-heavy - twinkle or wave
            mode = "treble"
        elif self._smooth_energy > HIGH_ENERGY_THRESHOLD:
            # High overall energy - intense animation
            mode = "intense"
        elif self._smooth_energy > MID_ENERGY_THRESHOLD:
            # Medium energy - flowing gradient
            mode = "flowing"
        else:
            # Low energy - gentle colors
            mode = "gentle"

        # Send theme command (throttled)
        mode_changed = mode != self._current_mode
        theme_due = (now - self._last_theme_time) >= THEME_INTERVAL

        if mode_changed or theme_due:
            self._send_mode(mode, color1, color2, is_beat)
            self._current_mode = mode
            self._last_theme_time = now
        elif is_beat and (now - self._last_color_time) >= COLOR_INTERVAL:
            # On beats between theme updates, flash the color brighter
            bright = self._brighten(color1)
            self.ble.send(f"COLOR{bright[0]:03d}{bright[1]:03d}{bright[2]:03d}")
            self._last_color_time = now

    def _send_mode(self, mode: str, color1: tuple, color2: tuple, is_beat: bool):
        """Send the appropriate theme command for the current mode."""
        r1, g1, b1 = color1
        r2, g2, b2 = color2

        if mode == "quiet":
            # Gentle pulsing in muted colors
            cr, cg, cb = max(30, r1 // 3), max(20, g1 // 3), max(40, b1 // 3)
            self.ble.send(f"THEME.PULSING1.{cr},{cg},{cb},")

        elif mode == "bass_heavy":
            # Fire effect with bass-derived warm colors
            if is_beat or self._beat_count % 3 == 0:
                self.ble.send(f"THEME.FIRE2.{r1},{g1},{b1},{r2},{g2},{b2},")
            else:
                self.ble.send(f"THEME.BEAT2.{r1},{g1},{b1},{r2},{g2},{b2},")

        elif mode == "treble":
            # Twinkle/sparkle for high frequencies
            self.ble.send(f"THEME.TWINKLE1.{r1},{g1},{b1},{r2},{g2},{b2},")

        elif mode == "intense":
            # Fast beat animation with vivid colors
            if self._beat_count % 2 == 0:
                self.ble.send(f"THEME.BEAT3.{r1},{g1},{b1},{r2},{g2},{b2},")
            else:
                self.ble.send(f"THEME.WAVE1.{r1},{g1},{b1},{r2},{g2},{b2},")

        elif mode == "flowing":
            # Gradient flowing between the two derived colors
            self.ble.send(f"THEME.GRADIENT2.{r1},{g1},{b1},{r2},{g2},{b2},{(r1+r2)//2},{(g1+g2)//2},{(b1+b2)//2},")

        elif mode == "gentle":
            # Slow gradient
            self.ble.send(f"THEME.GRADIENT1.{r1},{g1},{b1},{r2},{g2},{b2},")

    def _energy_to_color(self, bass: float, mids: float, highs: float) -> tuple[int, int, int]:
        """Map frequency energies to a primary RGB color."""
        # Bass -> red/orange, Mids -> green/teal, Highs -> blue/purple
        r = int(min(255, bass * 255 + highs * 60))
        g = int(min(255, mids * 200 + bass * 30))
        b = int(min(255, highs * 255 + mids * 40))

        # Ensure we always have some color
        if r + g + b < 30:
            r, g, b = 20, 10, 40

        return (r, g, b)

    def _energy_to_accent(self, bass: float, mids: float, highs: float) -> tuple[int, int, int]:
        """Map frequency energies to a contrasting accent color."""
        # Rotate the hue: highs -> warm, bass -> cool, mids -> purple
        r = int(min(255, highs * 200 + mids * 80))
        g = int(min(255, bass * 150 + highs * 50))
        b = int(min(255, mids * 220 + bass * 80))

        if r + g + b < 30:
            r, g, b = 40, 10, 60

        return (r, g, b)

    def _brighten(self, color: tuple[int, int, int]) -> tuple[int, int, int]:
        """Brighten a color for beat flashes."""
        r, g, b = color
        boost = 1.6
        return (
            int(min(255, r * boost)),
            int(min(255, g * boost)),
            int(min(255, b * boost)),
        )

    def _band_energy(
        self, fft: np.ndarray, freqs: np.ndarray, low: float, high: float
    ) -> float:
        """Calculate energy in a frequency band."""
        mask = (freqs >= low) & (freqs <= high)
        if not np.any(mask):
            return 0.0
        return float(np.mean(fft[mask] ** 2))
