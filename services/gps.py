"""Foreground service: BT reader + NMEA parser + mock location injector.

This runs in a separate process (declared via python-for-android's
--service flag). It keeps the location injection alive even when the main
app is backgrounded (e.g. when the user opens Organic Maps).

The service:
  1. Connects to the JDY-18 via Bluetooth SPP.
  2. Reads NMEA sentences.
  3. Parses them into a GpsFix.
  4. Injects the position as a mock location.
  5. Writes the GPS status to a JSON file (for the main app's UI to read).
  6. Reconnects automatically if the BT connection drops.

Build declaration (in the python-for-android command):
  --service=gps:services/gps.py:foreground:sticky:foregroundServiceType=location
"""

import json
import logging
import os
import sys
import time
from typing import Optional

# Ensure the app root is on sys.path (the service runs in a separate process
# and the working directory may differ from the app root).
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gps.bt_reader import BtSppReader  # noqa: E402
from gps.nmea import NmeaParser  # noqa: E402
from gps.ubx import command_bytes, DEFAULT_CONSTELLATION  # noqa: E402
from inject.mock_location import MockLocationInjector  # noqa: E402

# --- Configuration ---------------------------------------------------------
# MAC address of the JDY-18 (set via env var or edit here).
MAC_ADDRESS = os.environ.get("GPS_BT_MAC", "3C:A5:19:7A:F0:F8")
# Where the service writes the GPS status (the main app reads this).
STATUS_FILE = os.environ.get(
    "GPS_STATUS_FILE",
    "/data/data/org.gnssbridge.app/files/gps_status.json",
)
# The UI writes the requested constellation ("GPS" or "GLONASS") here; the
# service polls it and sends the matching UBX-CFG-GNSS command over BLE.
COMMAND_FILE = os.environ.get(
    "GPS_COMMAND_FILE",
    "/data/data/org.gnssbridge.app/files/gps_command.txt",
)
# The service writes the current (last-sent) constellation here; the UI reads
# it to display the active selection and to restore it on restart.
SELECTION_FILE = os.environ.get(
    "GPS_SELECTION_FILE",
    "/data/data/org.gnssbridge.app/files/gps_selection.txt",
)
# Seconds to wait before reconnecting after a dropped connection.
RECONNECT_DELAY = int(os.environ.get("GPS_RECONNECT_DELAY", "5"))
# How often (seconds) to poll the command file for a constellation switch.
COMMAND_POLL_INTERVAL = float(os.environ.get("GPS_COMMAND_POLL", "1.0"))
# Default accuracy (meters) to report for the injected location.
DEFAULT_ACCURACY = float(os.environ.get("GPS_ACCURACY", "10"))
# ---------------------------------------------------------------------------

log = logging.getLogger("gnss-bridge.service")


def write_status(status: dict):
    """Write the GPS status to a JSON file (for the main app's UI)."""
    try:
        directory = os.path.dirname(STATUS_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f)
    except Exception:
        log.exception("Error writing status file")


def _read_text_file(path: str) -> Optional[str]:
    """Read a text file, returning None if it is missing or unreadable."""
    try:
        with open(path) as f:
            return f.read().strip().upper()
    except (FileNotFoundError, OSError):
        return None


def read_command() -> Optional[str]:
    """Read the requested constellation from the command file.

    Returns ``"GPS"``, ``"GLONASS"``, or None if the file is missing/invalid.
    """
    value = _read_text_file(COMMAND_FILE)
    if value in ("GPS", "GLONASS"):
        return value
    return None


def read_selection() -> str:
    """Read the last-sent constellation (for startup restore).

    Falls back to the default (GPS) if the file is missing or invalid.
    """
    value = _read_text_file(SELECTION_FILE)
    if value in ("GPS", "GLONASS"):
        return value
    return DEFAULT_CONSTELLATION


def write_selection(constellation: str):
    """Persist the current constellation selection."""
    try:
        directory = os.path.dirname(SELECTION_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(SELECTION_FILE, "w") as f:
            f.write(constellation)
    except Exception:
        log.exception("Error writing selection file")


def send_constellation(reader: BtSppReader, constellation: str) -> bool:
    """Send the UBX-CFG-GNSS command for ``constellation`` over BLE.

    On success, persists the selection so it is restored on the next (re)start.
    Returns True if the write was queued, False otherwise.
    """
    try:
        data = command_bytes(constellation)
    except ValueError:
        log.exception("Invalid constellation: %s", constellation)
        return False
    ok = reader.write(data)
    log.info("Sent UBX-CFG-GNSS for %s: %s",
             constellation, "queued" if ok else "FAILED")
    if ok:
        write_selection(constellation)
    return ok


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log.info("GPS bridge service starting (MAC=%s)", MAC_ADDRESS)

    parser = NmeaParser()
    injector = MockLocationInjector()

    def on_line(line: str):
        fix = parser.feed(line)
        if fix is None:
            return
        if fix.is_valid():
            # Derive the reported accuracy from HDOP when available (more
            # realistic than a fixed value); DEFAULT_ACCURACY is the floor.
            accuracy = DEFAULT_ACCURACY
            if fix.hdop:
                accuracy = max(DEFAULT_ACCURACY, fix.hdop)
            try:
                injector.inject(
                    lat=fix.lat,
                    lon=fix.lon,
                    accuracy=accuracy,
                    altitude=fix.altitude,
                    speed_mps=(fix.speed_kmh / 3.6) if fix.speed_kmh else None,
                    bearing=fix.course,
                )
            except Exception:
                log.exception("Error injecting location")
            status = fix.to_dict()
            status["state"] = "fix"
            write_status(status)
        else:
            status = fix.to_dict()
            status["state"] = "no_fix"
            write_status(status)

    def on_state_change(state: str):
        log.info("BT state change: %s", state)
        write_status({"state": state})

    reader = BtSppReader(MAC_ADDRESS)
    reader.on_line = on_line
    reader.on_state_change = on_state_change

    # Main loop: connect, read, inject; reconnect on failure.
    last_sent = None
    while True:
        try:
            reader.connect()
            reader.start()
            log.info("Connected and reading NMEA")

            # Restore the last constellation selection on (re)connect, so the
            # receiver is in the expected band even after a restart.
            startup_sel = read_selection()
            if send_constellation(reader, startup_sel):
                last_sent = startup_sel

            # Wait for the reader thread to stop (connection dropped), polling
            # the command file for a constellation switch in the meantime.
            while reader.running:
                cmd = read_command()
                if cmd is not None and cmd != last_sent:
                    if send_constellation(reader, cmd):
                        last_sent = cmd
                time.sleep(COMMAND_POLL_INTERVAL)
        except Exception:
            log.exception("Error connecting to BT device")
        finally:
            reader.disconnect()
        log.info("Reconnecting in %d seconds...", RECONNECT_DELAY)
        time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()
