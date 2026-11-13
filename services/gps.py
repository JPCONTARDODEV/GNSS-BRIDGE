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

# Ensure the app root is on sys.path (the service runs in a separate process
# and the working directory may differ from the app root).
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gps.bt_reader import BtSppReader  # noqa: E402
from gps.nmea import NmeaParser  # noqa: E402
from inject.mock_location import MockLocationInjector  # noqa: E402

# --- Configuration ---------------------------------------------------------
# MAC address of the JDY-18 (set via env var or edit here).
MAC_ADDRESS = os.environ.get("GPS_BT_MAC", "3C:A5:19:7A:F0:F8")
# Where the service writes the GPS status (the main app reads this).
STATUS_FILE = os.environ.get(
    "GPS_STATUS_FILE",
    "/data/data/org.gnssbridge.app/files/gps_status.json",
)
# Seconds to wait before reconnecting after a dropped connection.
RECONNECT_DELAY = int(os.environ.get("GPS_RECONNECT_DELAY", "5"))
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
    while True:
        try:
            reader.connect()
            reader.start()
            log.info("Connected and reading NMEA")
            # Wait for the reader thread to stop (connection dropped).
            while reader.running:
                time.sleep(1)
        except Exception:
            log.exception("Error connecting to BT device")
        finally:
            reader.disconnect()
        log.info("Reconnecting in %d seconds...", RECONNECT_DELAY)
        time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()
