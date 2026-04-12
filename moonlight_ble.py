"""Moonside Halo BLE connection manager.

Maintains a persistent BLE connection and provides a thread-safe command interface.
Runs its own asyncio event loop in a background thread.
"""

import asyncio
import logging
import queue
import threading
import time

from bleak import BleakClient, BleakScanner

log = logging.getLogger("moonlight.ble")

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

# The hardware advertises itself with this BLE name prefix — do not rename.
DEVICE_NAME_PREFIX = "MOONSIDE"


class MoonlightBLE:
    """Thread-safe BLE controller for Moonside Halo lamps."""

    def __init__(self, device_address: str | None = None):
        self.device_address = device_address
        self._client: BleakClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._cmd_queue: queue.Queue[str | None] = queue.Queue()
        self._connected = threading.Event()
        self._stopping = False
        self._on_connection_change: callable = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self, on_connection_change: callable = None):
        """Start the BLE manager in a background thread."""
        self._on_connection_change = on_connection_change
        self._stopping = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the BLE manager and disconnect."""
        self._stopping = True
        self._cmd_queue.put(None)  # sentinel to wake the loop
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        if self._thread:
            self._thread.join(timeout=5)

    def send(self, command: str):
        """Queue a command to send to the lamp. Thread-safe."""
        if not self._stopping:
            self._cmd_queue.put(command)

    def send_color(self, r: int, g: int, b: int):
        """Send a solid color command."""
        self.send("LEDON")
        self.send(f"COLOR{r:03d}{g:03d}{b:03d}")

    def send_brightness(self, value: int):
        """Send brightness (0-120)."""
        self.send(f"BRIGH{value:03d}")

    def send_theme(self, theme: str, colors: list[tuple[int, int, int]]):
        """Send a theme/effect command with color parameters."""
        color_str = ",".join(f"{r},{g},{b}" for r, g, b in colors) + ","
        self.send(f"THEME.{theme}.{color_str}")

    def send_off(self):
        self.send("LEDOFF")

    def send_on(self):
        self.send("LEDON")

    def _run_loop(self):
        """Run the asyncio event loop in this thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception:
            log.exception("BLE loop crashed")
        finally:
            self._loop.close()

    async def _main(self):
        """Main async loop: connect, then process commands."""
        while not self._stopping:
            try:
                await self._connect()
                if self._client and self._client.is_connected:
                    self._connected.set()
                    self._notify_connection(True)
                    await self._process_commands()
            except Exception:
                log.exception("BLE connection error")
            finally:
                self._connected.clear()
                self._notify_connection(False)

            if not self._stopping:
                log.info("Reconnecting in 3s...")
                await asyncio.sleep(3)

    async def _connect(self):
        """Scan and connect to the Moonside Halo lamp."""
        log.info("Scanning for Moonside Halo lamp...")

        if self.device_address:
            self._client = BleakClient(self.device_address)
            await self._client.connect(timeout=10)
            log.info(f"Connected to {self.device_address}")
            return

        device = await BleakScanner.find_device_by_name(
            DEVICE_NAME_PREFIX, timeout=10
        )
        if not device:
            # Try prefix match
            devices = await BleakScanner.discover(timeout=10)
            for d in devices:
                if d.name and d.name.startswith(DEVICE_NAME_PREFIX):
                    device = d
                    break

        if not device:
            raise ConnectionError("No Moonside Halo lamp found")

        log.info(f"Found {device.name} ({device.address})")
        self._client = BleakClient(device.address)
        await self._client.connect(timeout=10)
        log.info("Connected")

    async def _process_commands(self):
        """Process queued commands while connected."""
        while not self._stopping and self._client and self._client.is_connected:
            try:
                cmd = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._cmd_queue.get(timeout=0.2)
                )
            except queue.Empty:
                continue

            if cmd is None:
                break

            try:
                await self._write(cmd)
            except Exception:
                log.exception(f"Failed to send: {cmd}")
                break

    async def _write(self, command: str):
        """Write a command string to the lamp."""
        if self._client and self._client.is_connected:
            data = command.encode("utf-8")
            await self._client.write_gatt_char(NUS_TX_UUID, data, response=True)
            log.debug(f"Sent: {command}")
            # Small delay between rapid commands for lamp to process
            await asyncio.sleep(0.05)

    async def _shutdown(self):
        """Disconnect gracefully."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception:
                pass

    def _notify_connection(self, connected: bool):
        if self._on_connection_change:
            try:
                self._on_connection_change(connected)
            except Exception:
                pass
