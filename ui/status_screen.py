"""Kivy status screen: shows GPS state and controls the service.

Reads the GPS status from the JSON file written by the foreground service,
and provides buttons to start/stop the service.
"""

import json
import logging
import os
from typing import Optional

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.clock import Clock

log = logging.getLogger("gnss-bridge.ui")

# Same path the service writes to.
STATUS_FILE = os.environ.get(
    "GPS_STATUS_FILE",
    "/data/data/org.gnssbridge.app/files/gps_status.json",
)
# The UI writes the requested constellation here (the service polls it).
COMMAND_FILE = os.environ.get(
    "GPS_COMMAND_FILE",
    "/data/data/org.gnssbridge.app/files/gps_command.txt",
)
# The service writes the current selection here (the UI reads it).
SELECTION_FILE = os.environ.get(
    "GPS_SELECTION_FILE",
    "/data/data/org.gnssbridge.app/files/gps_selection.txt",
)
# How often (seconds) to poll the status file.
POLL_INTERVAL = 1.0

# Colors for the constellation selection buttons.
COLOR_SELECTED = [0.13, 0.55, 0.13, 1]    # green
COLOR_UNSELECTED = [0.22, 0.22, 0.22, 1]  # gray


class StatusScreen(BoxLayout):
    """A vertical box layout showing the GPS status and service controls."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = 20
        self.spacing = 10

        # --- Status labels ---
        self.bt_label = Label(text="BT: desconectado", font_size=20)
        self.fix_label = Label(text="GPS: sin fix", font_size=20)
        self.pos_label = Label(text="Lat: --  Lon: --", font_size=18)
        self.sats_label = Label(text="Satélites: --  HDOP: --", font_size=18)
        self.speed_label = Label(text="Velocidad: -- km/h", font_size=18)
        self.update_label = Label(text="Última actualización: --", font_size=16)

        for w in (
            self.bt_label,
            self.fix_label,
            self.pos_label,
            self.sats_label,
            self.speed_label,
            self.update_label,
        ):
            w.halign = "center"
            self.add_widget(w)

        # --- Spacer ---
        spacer = BoxLayout(size_hint_y=0.1)
        self.add_widget(spacer)

        # --- Constellation selection (GPS / GLONASS) ---
        self.const_label = Label(
            text="Constelación (NEO-7M: una banda a la vez)", font_size=14)
        self.const_label.halign = "center"
        self.add_widget(self.const_label)

        const_box = BoxLayout(orientation="horizontal", spacing=10)
        self.gps_btn = Button(text="GPS", font_size=18)
        self.gps_btn.bind(on_press=self._on_constellation)
        const_box.add_widget(self.gps_btn)
        self.glo_btn = Button(text="GLONASS", font_size=18)
        self.glo_btn.bind(on_press=self._on_constellation)
        const_box.add_widget(self.glo_btn)
        self.add_widget(const_box)

        # Restore the last constellation selection (default GPS).
        self._current_constellation = self._read_selection()
        self._set_selection(self._current_constellation)

        # --- Buttons ---
        self.start_btn = Button(text="Iniciar servicio", font_size=18)
        self.start_btn.bind(on_press=self._on_start)
        self.add_widget(self.start_btn)

        self.stop_btn = Button(text="Detener servicio", font_size=18)
        self.stop_btn.bind(on_press=self._on_stop)
        self.add_widget(self.stop_btn)

        # --- Poll the status file ---
        self._clock_event = Clock.schedule_interval(self._update_status, POLL_INTERVAL)

    def _update_status(self, dt):
        """Read the status file and update the labels."""
        try:
            with open(STATUS_FILE) as f:
                status = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return  # No status yet; keep the last display.

        state = status.get("state", "unknown")
        if state == "connected":
            self.bt_label.text = "BT: conectado"
        elif state == "connecting":
            self.bt_label.text = "BT: conectando..."
        elif state == "disconnected":
            self.bt_label.text = "BT: desconectado"
        elif state == "fix":
            self.bt_label.text = "BT: conectado"
        elif state == "no_fix":
            self.bt_label.text = "BT: conectado (sin fix)"

        if state == "fix":
            self.fix_label.text = "GPS: FIX"
            lat = status.get("lat")
            lon = status.get("lon")
            if lat is not None and lon is not None:
                self.pos_label.text = f"Lat: {lat:.6f}  Lon: {lon:.6f}"
            sats = status.get("sats")
            hdop = status.get("hdop")
            self.sats_label.text = (
                f"Satélites: {sats if sats is not None else '--'}  "
                f"HDOP: {hdop if hdop is not None else '--'}"
            )
            speed = status.get("speed_kmh")
            self.speed_label.text = (
                f"Velocidad: {speed if speed is not None else '--'} km/h"
            )
            ts = status.get("timestamp")
            self.update_label.text = (
                f"Última actualización: {ts if ts else '--'}"
            )
        elif state == "no_fix":
            self.fix_label.text = "GPS: sin fix"
        else:
            self.fix_label.text = f"GPS: {state}"

    def _on_start(self, instance):
        """Start the foreground service."""
        log.info("User requested: start service")
        try:
            from services.control import start_service

            start_service()
            self.bt_label.text = "BT: iniciando..."
        except Exception:
            log.exception("Error starting service")
            self.bt_label.text = "BT: error al iniciar"

    def _on_stop(self, instance):
        """Stop the foreground service."""
        log.info("User requested: stop service")
        try:
            from services.control import stop_service

            stop_service()
            self.bt_label.text = "BT: detenido"
        except Exception:
            log.exception("Error stopping service")
            self.bt_label.text = "BT: error al detener"

    def _on_constellation(self, instance):
        """Handle a constellation button press: request the switch.

        Writes the requested constellation to the command file (the service
        polls it and sends the UBX-CFG-GNSS command over BLE) and highlights the
        button optimistically. If the BLE write fails, the log shows
        ``Sent UBX-CFG-GNSS for ...: FAILED`` and the selection file is left
        unchanged (so the next restart restores the last *successful* choice).
        """
        constellation = "GPS" if instance is self.gps_btn else "GLONASS"
        log.info("User selected constellation: %s", constellation)
        try:
            directory = os.path.dirname(COMMAND_FILE)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(COMMAND_FILE, "w") as f:
                f.write(constellation)
        except Exception:
            log.exception("Error writing command file")
            return
        self._current_constellation = constellation
        self._set_selection(constellation)

    def _set_selection(self, constellation):
        """Highlight the selected constellation button (green) and dim the
        other (gray)."""
        if constellation == "GPS":
            self.gps_btn.background_color = COLOR_SELECTED
            self.glo_btn.background_color = COLOR_UNSELECTED
        else:
            self.glo_btn.background_color = COLOR_SELECTED
            self.gps_btn.background_color = COLOR_UNSELECTED

    def _read_selection(self) -> str:
        """Read the current constellation from the selection file.

        Falls back to GPS if the file is missing or invalid.
        """
        try:
            with open(SELECTION_FILE) as f:
                value = f.read().strip().upper()
            if value in ("GPS", "GLONASS"):
                return value
        except (FileNotFoundError, OSError):
            pass
        return "GPS"

    def on_pre_unwind(self):
        """Clean up the clock event when the widget is destroyed."""
        if self._clock_event is not None:
            self._clock_event.cancel()
            self._clock_event = None
