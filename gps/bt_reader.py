"""Bluetooth LE (BLE) GATT reader for the u-blox NEO-7M (via JDY-18).

The JDY-18 is a Bluetooth **LE** module (not classic SPP). It exposes the
UART data (NMEA sentences from the NEO-7M) as a BLE characteristic that we
read via GATT notifications.

Uses pyjnius to access the Android Bluetooth LE APIs. A small Java helper
class (``org.gnssbridge.app.GpsGatt``, added to the APK via p4a
``--add-source``) extends the abstract ``BluetoothGattCallback`` and forwards
events to a Python callback, because pyjnius can only implement Java
*interfaces* (it uses ``java.lang.reflect.Proxy``), not abstract classes.

Public interface (unchanged from the old SPP reader, so ``services/gps.py``
needs no changes):
    reader = BtSppReader("XX:XX:XX:XX:XX:XX")
    reader.on_line = lambda line: print(line)
    reader.on_state_change = lambda state: print(state)
    reader.connect()
    reader.start()
    ...
    reader.stop()
    reader.disconnect()

Note on baud rate: the baud rate is a property of the UART link between the
NEO-7M and the JDY-18, NOT the Bluetooth link. The JDY-18 must be configured
to the same baud rate as the NEO-7M (default 4800 for u-blox, or 9600 if you
reconfigure the NEO-7M). This is done once via AT commands to the JDY-18.
"""

import logging
import queue
import threading
from typing import Callable, Optional

log = logging.getLogger("gnss-bridge.bt")

# CCCD (Client Characteristic Configuration Descriptor) UUID.
CCCD_UUID = "00002902-0000-1000-8000-00805F9B34FB"

# android.bluetooth.BluetoothProfile state constants.
STATE_DISCONNECTED = 0
STATE_CONNECTING = 1
STATE_CONNECTED = 2
STATE_DISCONNECTING = 3

# GATT status code for success.
GATT_SUCCESS = 0

# Connection / discovery timeouts (seconds).
CONNECT_TIMEOUT = 15
DISCOVER_TIMEOUT = 10


def _android():
    """Import the pyjnius Android classes. Raises ImportError if not on
    Android (e.g. when running on a PC for testing)."""
    from jnius import autoclass

    return {
        "BluetoothAdapter": autoclass("android.bluetooth.BluetoothAdapter"),
        "BluetoothDevice": autoclass("android.bluetooth.BluetoothDevice"),
        "BluetoothGatt": autoclass("android.bluetooth.BluetoothGatt"),
        "BluetoothGattCharacteristic": autoclass(
            "android.bluetooth.BluetoothGattCharacteristic"),
        "BluetoothGattDescriptor": autoclass(
            "android.bluetooth.BluetoothGattDescriptor"),
        "UUID": autoclass("java.util.UUID"),
        "GpsGatt": autoclass("org.gnssbridge.app.GpsGatt"),
    }


def _get_context():
    """Obtain an Android Context from the running app/service.

    The service runs in its own process, so ``PythonActivity.mActivity`` is
    not available there. We prefer the running ``PythonService`` instance
    (set by p4a right before Python starts), then fall back to the
    application instance, then the main activity.
    """
    from jnius import autoclass

    # Primary: the running PythonService instance.
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


# pyjnius is only available on Android; guard the import so the module can be
# imported on a PC (e.g. for py_compile / unit tests).
try:
    from jnius import PythonJavaClass, java_method
    _HAS_JNIUS = True
except ImportError:  # pragma: no cover - PC only
    _HAS_JNIUS = False


if _HAS_JNIUS:
    class _GpsGattCallback(PythonJavaClass):
        """Python implementation of the ``GpsGattCallback`` Java interface.

        Created as a pyjnius proxy and passed to the Java ``GpsGatt`` class,
        which forwards GATT events to it. All methods are called on Android's
        binder thread; they must not block (they push work into a queue).
        """

        __javainterfaces__ = ['org/gnssbridge/app/GpsGattCallback']
        # Use the app classloader (not the system one) so the proxy can see
        # the GpsGattCallback interface compiled into the APK.
        __javacontext__ = 'app'

        def __init__(self, reader):
            self._reader = reader
            super().__init__()

        @java_method('(II)V')
        def onConnectionStateChange(self, status, newState):
            self._reader._on_connection_state_change(status, newState)

        @java_method('(I)V')
        def onServicesDiscovered(self, status):
            self._reader._on_services_discovered(status)

        @java_method('(Ljava/lang/String;[B)V')
        def onCharacteristicChanged(self, uuid, value):
            self._reader._on_characteristic_changed(uuid, value)
else:  # pragma: no cover - PC only
    _GpsGattCallback = None


class BtSppReader:
    """Reads NMEA sentences from a Bluetooth LE device (JDY-18) via GATT.

    (The class name is kept for compatibility with ``services/gps.py``; the
    transport is BLE GATT, not classic SPP.)

    Usage:
        reader = BtSppReader("XX:XX:XX:XX:XX:XX")
        reader.on_line = lambda line: print(line)
        reader.on_state_change = lambda state: print(state)
        reader.connect()
        reader.start()
        ...
        reader.stop()
        reader.disconnect()
    """

    def __init__(self, mac_address: str):
        self.mac_address = mac_address
        self.on_line: Optional[Callable[[str], None]] = None
        self.on_state_change: Optional[Callable[[str], None]] = None

        self._cls = None            # lazy-loaded Android classes
        self._gatt = None           # android.bluetooth.BluetoothGatt
        self._gatt_callback = None  # org.gnssbridge.app.GpsGatt (Java)
        self._py_callback = None    # _GpsGattCallback (Python proxy)
        self._characteristic = None

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._connected = False

        # Synchronization between the binder-thread callbacks and the
        # Python threads.
        self._connected_event = threading.Event()
        self._disconnected_event = threading.Event()
        self._services_event = threading.Event()
        self._data_queue = queue.Queue()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def running(self) -> bool:
        """True while the reader (worker) thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _state(self, state: str):
        log.info("BT state: %s", state)
        if self.on_state_change:
            try:
                self.on_state_change(state)
            except Exception:
                log.exception("on_state_change callback failed")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def connect(self):
        """Establish the BLE GATT connection. Raises on failure.

        ``connectGatt`` is asynchronous: the actual connection is reported via
        the ``onConnectionStateChange`` callback, so we wait for it here.
        """
        if _GpsGattCallback is None:
            raise RuntimeError("pyjnius is not available (not on Android?)")

        self._cls = _android()
        context = _get_context()
        adapter = self._cls["BluetoothAdapter"].getDefaultAdapter()
        if adapter is None:
            raise RuntimeError("Bluetooth is not available on this device")
        if not adapter.isEnabled():
            raise RuntimeError("Bluetooth is disabled; enable it in settings")

        device = adapter.getRemoteDevice(self.mac_address)

        # Reset the synchronization events for a fresh attempt.
        self._connected_event.clear()
        self._disconnected_event.clear()
        self._services_event.clear()

        # Build the Java callback (wraps the Python proxy).
        self._py_callback = _GpsGattCallback(self)
        self._gatt_callback = self._cls["GpsGatt"](self._py_callback)

        self._state("connecting")
        gatt = device.connectGatt(context, False, self._gatt_callback)
        if gatt is None:
            raise RuntimeError(
                "connectGatt returned null (connection failed to start)")
        self._gatt = gatt

        # Wait for the connection to be established (or rejected).
        if not self._connected_event.wait(timeout=CONNECT_TIMEOUT):
            self._close_gatt()
            raise RuntimeError(
                "BLE connection timed out or was rejected by the device")
        if self._disconnected_event.is_set():
            self._close_gatt()
            raise RuntimeError(
                "BLE connection dropped immediately after connecting")

        self._connected = True
        self._state("connected")
        log.info("BLE GATT connected to %s", self.mac_address)

    def disconnect(self):
        """Tear down the BLE GATT connection."""
        self.stop()
        self._close_gatt()
        self._connected = False
        self._state("disconnected")

    def _close_gatt(self):
        if self._gatt is not None:
            try:
                self._gatt.disconnect()
            except Exception:
                log.exception("Error disconnecting GATT")
            try:
                self._gatt.close()
            except Exception:
                log.exception("Error closing GATT")
        self._gatt = None

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def start(self):
        """Discover services, enable notifications, start the worker thread.

        Must be called after a successful ``connect()``. Raises on failure.
        """
        if not self._connected:
            raise RuntimeError("Not connected; call connect() first")
        if self._thread and self._thread.is_alive():
            return
        gatt = self._gatt
        if gatt is None:
            raise RuntimeError("No GATT connection")

        # Discover services (asynchronous; reported via onServicesDiscovered).
        self._services_event.clear()
        gatt.discoverServices()
        if not self._services_event.wait(timeout=DISCOVER_TIMEOUT):
            raise RuntimeError("BLE service discovery timed out")

        characteristic = self._find_serial_characteristic(gatt)
        if characteristic is None:
            raise RuntimeError(
                "Could not find a serial (NOTIFY/READ) characteristic on the "
                "device")
        self._characteristic = characteristic
        log.info("Selected characteristic %s", characteristic.getUuid())

        # Enable notifications on the characteristic.
        try:
            gatt.setCharacteristicNotification(characteristic, True)
        except Exception:
            log.exception("setCharacteristicNotification failed")
        self._enable_notifications(gatt, characteristic)

        # Start the worker thread that turns the byte stream into lines.
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self._state("reading")
        log.info("BLE notifications enabled; reading NMEA")

    def stop(self):
        """Stop the worker thread."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    # ------------------------------------------------------------------
    # GATT helpers
    # ------------------------------------------------------------------
    def _find_serial_characteristic(self, gatt):
        """Pick the characteristic that carries the serial (NMEA) data.

        Prefers a characteristic with the NOTIFY property, then READ. We do not
        hardcode a UUID because the JDY-18 uses a vendor-specific service.
        """
        char_class = self._cls["BluetoothGattCharacteristic"]
        try:
            prop_notify = char_class.PROPERTY_NOTIFY
            prop_read = char_class.PROPERTY_READ
        except Exception:
            prop_notify = 0x0100
            prop_read = 0x0080

        services = gatt.getServices()
        if services is None:
            return None
        notify_char = None
        read_char = None
        for i in range(services.size()):
            service = services.get(i)
            if service is None:
                continue
            chars = service.getCharacteristics()
            if chars is None:
                continue
            for j in range(chars.size()):
                char = chars.get(j)
                if char is None:
                    continue
                try:
                    props = char.getProperties()
                except Exception:
                    continue
                if props & prop_notify and notify_char is None:
                    notify_char = char
                elif props & prop_read and read_char is None:
                    read_char = char
        return notify_char or read_char

    def _enable_notifications(self, gatt, characteristic):
        """Write the CCCD to actually turn notifications on."""
        try:
            cccd_uuid = self._cls["UUID"].fromString(CCCD_UUID)
            descriptor = characteristic.getDescriptor(cccd_uuid)
        except Exception:
            log.exception("Could not look up CCCD descriptor")
            return
        if descriptor is None:
            log.warning("No CCCD descriptor; notifications may not start")
            return
        try:
            # Python bytearray is auto-converted to Java byte[] by pyjnius.
            value = bytearray(2)
            value[0] = 0x01  # 0x0001 == notifications enabled
            value[1] = 0x00
            descriptor.setValue(value)
            gatt.writeDescriptor(descriptor)
            log.info("Wrote CCCD to enable notifications")
        except Exception:
            log.exception("Failed to write CCCD")

    # ------------------------------------------------------------------
    # Binder-thread callbacks (called by the Java GpsGatt wrapper)
    # ------------------------------------------------------------------
    def _on_connection_state_change(self, status, new_state):
        log.info("GATT onConnectionStateChange status=%s newState=%s",
                 status, new_state)
        if new_state == STATE_CONNECTED:
            self._connected_event.set()
        elif new_state == STATE_DISCONNECTED:
            self._disconnected_event.set()
            self._connected = False
            self._state("disconnected")

    def _on_services_discovered(self, status):
        log.info("GATT onServicesDiscovered status=%s", status)
        self._services_event.set()

    def _on_characteristic_changed(self, uuid, value):
        # Diagnostic: confirm the GATT callback is reaching Python.
        log.info("onCharacteristicChanged fired: uuid=%s value_is_none=%s",
                 uuid, value is None)
        if value is None:
            return
        # ``value`` is a pyjnius ByteArray (a bytearray subclass) or bytes.
        try:
            data = bytes(value)
        except Exception:
            try:
                data = bytes(bytearray(value))
            except Exception:
                log.exception("Could not convert characteristic value")
                return
        self._data_queue.put(data)

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------
    def _read_loop(self):
        """Turn the incoming byte stream into NMEA lines and call on_line."""
        buffer = bytearray()

        def emit(buf):
            line = bytes(buf).decode("ascii", "ignore").strip()
            if line and self.on_line:
                try:
                    self.on_line(line)
                except Exception:
                    log.exception("on_line callback failed")

        while not self._stop.is_set() and not self._disconnected_event.is_set():
            try:
                data = self._data_queue.get(timeout=1)
            except queue.Empty:
                continue
            if data is None:  # sentinel
                break
            for b in data:
                if b == 0x0A:  # \n
                    emit(buffer)
                    buffer.clear()
                elif b == 0x0D:  # \r (handled by \n)
                    pass
                else:
                    buffer.append(b)
        # Flush any trailing partial line.
        if buffer:
            emit(buffer)
        log.info("Reader thread exiting")
