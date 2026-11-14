package org.gnssbridge.app;

import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattService;
import java.util.List;
import java.util.UUID;

/**
 * Extends the abstract {@code android.bluetooth.BluetoothGattCallback} and
 * forwards the relevant GATT events to a Python {@link GpsGattCallback}.
 *
 * <p>pyjnius cannot implement {@code BluetoothGattCallback} directly because
 * it is an abstract *class* (pyjnius proxies can only implement interfaces).
 * This thin Java wrapper bridges the two.</p>
 *
 * <p>All callbacks arrive on Android's binder thread; the Python side must
 * not block here (it pushes data into a queue processed by a worker thread).</p>
 */
public class GpsGatt extends BluetoothGattCallback {

    private final GpsGattCallback callback;
    private BluetoothGatt gatt;

    // JDY-18 RX characteristic (write here to send data to the NEO-7M's UART
    // TX). 16-bit UUID 0x0000ffe2 expanded onto the 128-bit Bluetooth base.
    private static final UUID RX_CHAR_UUID =
            UUID.fromString("0000ffe2-0000-1000-8000-00805F9B34FB");

    public GpsGatt(GpsGattCallback callback) {
        this.callback = callback;
    }

    @Override
    public void onConnectionStateChange(BluetoothGatt gatt, int status, int newState) {
        // Keep a reference to the GATT object so write() can use it.
        if (newState == 2 /* STATE_CONNECTED */) {
            this.gatt = gatt;
        } else if (newState == 0 /* STATE_DISCONNECTED */) {
            this.gatt = null;
        }
        if (callback != null) {
            callback.onConnectionStateChange(status, newState);
        }
    }

    @Override
    public void onServicesDiscovered(BluetoothGatt gatt, int status) {
        if (callback != null) {
            callback.onServicesDiscovered(status);
        }
    }

    @Override
    public void onCharacteristicChanged(BluetoothGatt gatt,
                                        BluetoothGattCharacteristic characteristic) {
        if (callback != null) {
            byte[] value = characteristic.getValue();
            if (value != null) {
                callback.onCharacteristicChanged(
                        characteristic.getUuid().toString(), value);
            }
        }
    }

    /**
     * Write bytes to the RX characteristic (0000ffe2), forwarding them to the
     * NEO-7M's UART TX.
     *
     * <p>The write is asynchronous: it is queued on the GATT connection and the
     * final result is reported via {@code onCharacteristicWrite} (not forwarded
     * to Python). The boolean return value only indicates whether the write was
     * successfully *queued*.</p>
     *
     * @param value the bytes to write (e.g. a UBX-CFG-GNSS command).
     * @return true if the write was queued, false if not connected or the
     *         characteristic was not found.
     */
    public boolean write(byte[] value) {
        if (gatt == null || value == null) {
            return false;
        }
        BluetoothGattCharacteristic rxChar = findCharacteristic(RX_CHAR_UUID);
        if (rxChar == null) {
            return false;
        }
        rxChar.setValue(value);
        rxChar.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
        return gatt.writeCharacteristic(rxChar);
    }

    /**
     * Find a characteristic by UUID, searching every discovered service.
     *
     * <p>Uses {@code gatt.getServices()} + {@code service.getCharacteristic()}
     * (both available since API 18) rather than {@code gatt.getCharacteristic()}
     * (API 33+), so it compiles against older SDKs.</p>
     *
     * @param charUuid the characteristic UUID to look for.
     * @return the matching characteristic, or null if not found.
     */
    private BluetoothGattCharacteristic findCharacteristic(UUID charUuid) {
        if (gatt == null) {
            return null;
        }
        List<BluetoothGattService> services = gatt.getServices();
        if (services == null) {
            return null;
        }
        for (BluetoothGattService service : services) {
            if (service == null) {
                continue;
            }
            BluetoothGattCharacteristic c = service.getCharacteristic(charUuid);
            if (c != null) {
                return c;
            }
        }
        return null;
    }
}
