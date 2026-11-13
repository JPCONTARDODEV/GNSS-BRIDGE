package org.gnssbridge.app;

import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;

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

    public GpsGatt(GpsGattCallback callback) {
        this.callback = callback;
    }

    @Override
    public void onConnectionStateChange(BluetoothGatt gatt, int status, int newState) {
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
}
