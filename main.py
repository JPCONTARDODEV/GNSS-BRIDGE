"""GNSS Bridge — main app entry point.

A Kivy app that shows the GPS status and controls the foreground service
that reads the external u-blox NEO-7M (via JDY-18 Bluetooth) and injects the
position as a mock location.

Run on Android (via python-for-android):
    p4a --requirements=kivy \
        --service=gps:services/gps.py:foreground:sticky:foregroundServiceType=location \
        --name "GNSS Bridge" --package org.gnssbridge.app \
        --private . --distribution gnssbridge

Run on a PC (for UI testing only; BT + injection won't work):
    python3 main.py
"""

import logging

from kivy.app import App
from kivy.core.window import Window

from ui.status_screen import StatusScreen

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gnss-bridge.main")


def request_permissions():
    """Request runtime permissions (Android 6+).

    On a PC (non-Android) this is a no-op.
    """
    try:
        from android.permissions import Permission, request_permissions

        request_permissions(
            [
                Permission.BLUETOOTH,
                Permission.ACCESS_FINE_LOCATION,
            ]
        )
        log.info("Permissions requested")
    except ImportError:
        log.info("Not on Android; skipping permission request")
    except Exception:
        log.exception("Error requesting permissions")


class GpsBridgeApp(App):
    """The Kivy app: shows the status screen."""

    def build(self):
        Window.size = (400, 700)
        Window.title = "GNSS Bridge"
        request_permissions()
        return StatusScreen()


if __name__ == "__main__":
    GpsBridgeApp().run()
