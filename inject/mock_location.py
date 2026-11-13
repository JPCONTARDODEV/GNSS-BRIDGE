"""Mock location injection via pyjnius.

Uses the Android LocationManager's test-provider API to inject a fake GPS
location. The app must be selected as the "Mock location app" in Android's
Developer Options (Settings > Developer options > Mock location app).

On Android 6 (the Moto E2), this is the standard way to override the phone's
location. The injected location is visible to all apps (including Organic
Maps), subject to each app's own handling of mock locations.

The LocationManager is obtained from a Context (Activity in the main app
process, or the service's Context in the foreground-service process).
"""

import logging
import time
from typing import Optional

log = logging.getLogger("gnss-bridge.inject")


def get_context():
    """Get an Android Context (Activity or service).

    The service runs in its own process, so ``PythonActivity.mActivity`` is
    not available there. We prefer the running ``PythonService`` instance
    (set by p4a right before Python starts), then fall back to the
    application instance, then the main activity.
    """
    from jnius import autoclass

    # Primary: the running PythonService instance (service process).
    try:
        PythonService = autoclass("org.kivy.android.PythonService")
        svc = PythonService.mService
        if svc is not None:
            return svc.getApplicationContext()
    except Exception:
        pass
    # Fallback: the application instance (works in any process).
    try:
        ActivityThread = autoclass("android.app.ActivityThread")
        app = ActivityThread.currentApplication()
        if app is not None:
            return app.getApplicationContext()
    except Exception:
        pass
    # Last resort: the main activity (only works in the main process).
    try:
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        act = PythonActivity.mActivity
        if act is not None:
            return act.getApplicationContext()
    except Exception:
        pass
    raise RuntimeError("Could not obtain an Android Context")


def _android():
    """Import the pyjnius Android classes. Raises ImportError if not on
    Android (e.g. when running on a PC for testing)."""
    from jnius import autoclass, cast

    return {
        "LocationManager": autoclass("android.location.LocationManager"),
        "Location": autoclass("android.location.Location"),
        "SystemClock": autoclass("android.os.SystemClock"),
        "cast": cast,
    }


class MockLocationInjector:
    """Injects a mock GPS location via the LocationManager test-provider API.

    Usage:
        injector = MockLocationInjector()
        injector.initialize()
        injector.inject(lat=..., lon=..., accuracy=10.0)
        ...
        injector.remove()
    """

    def __init__(self, provider_name: str = "gps", context=None):
        self.provider_name = provider_name
        self._context = context  # optional; if None, get_context() is used
        self._lm = None
        self._cls = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    def initialize(self):
        """Get the LocationManager and register the test provider.

        Raises RuntimeError if the app is not set as the mock location app.
        """
        if self._initialized:
            return
        self._cls = _android()
        context = self._context if self._context is not None else get_context()

        lm = context.getSystemService("location")
        self._lm = self._cls["cast"]("android.location.LocationManager", lm)

        # Register the test provider. We use "gps" as the provider name so it
        # overrides the real GPS.
        self._lm.addTestProvider(
            self.provider_name,  # provider
            False,               # requiresNetwork
            False,               # requiresSatellite
            False,               # requiresCell
            False,               # hasMonetaryCost
            True,                # supportsAltitude
            True,                # supportsSpeed
            True,                # supportsBearing
            3,                   # powerUsage (0=none, 1=low, 2=medium, 3=high)
            5,                   # accuracy  (0=coarse, 1=fine, 2=medium, 3=high, 4=very_high, 5=best)
        )

        self._initialized = True
        log.info("Mock location provider '%s' registered", self.provider_name)

    def inject(
        self,
        lat: float,
        lon: float,
        accuracy: float = 10.0,
        altitude: Optional[float] = None,
        speed_mps: Optional[float] = None,
        bearing: Optional[float] = None,
    ):
        """Inject a single mock location.

        lat/lon in decimal degrees. accuracy in meters. altitude in meters.
        speed_mps in m/s. bearing in degrees.
        """
        if not self._initialized:
            self.initialize()

        loc = self._cls["Location"](self.provider_name)
        loc.setLatitude(lat)
        loc.setLongitude(lon)
        loc.setAccuracy(accuracy)
        if altitude is not None:
            loc.setAltitude(altitude)
        if speed_mps is not None:
            loc.setSpeed(speed_mps)
        if bearing is not None:
            loc.setBearing(bearing)
        # Use the current time (in milliseconds since epoch).
        now_ms = int(time.time() * 1000)
        loc.setTime(now_ms)
        loc.setElapsedRealtimeNanos(self._cls["SystemClock"].elapsedRealtimeNanos())

        self._lm.setTestProviderLocation(self.provider_name, loc)
        log.debug("Injected mock location: %.6f, %.6f", lat, lon)

    def remove(self):
        """Remove the test provider (stop injecting)."""
        if self._lm is not None:
            try:
                self._lm.removeTestProvider(self.provider_name)
                log.info("Mock location provider '%s' removed", self.provider_name)
            except Exception:
                log.exception("Error removing test provider")
        self._lm = None
        self._initialized = False
