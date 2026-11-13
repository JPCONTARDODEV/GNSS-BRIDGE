package org.gnssbridge.app;

/**
 * Callback interface implemented by the Python side (via pyjnius
 * {@code javainterface}).
 *
 * <p>The {@link GpsGatt} class (which extends the abstract
 * {@code android.bluetooth.BluetoothGattCallback}) forwards the relevant GATT
 * events to this interface. pyjnius can only implement Java *interfaces*
 * (it uses {@code java.lang.reflect.Proxy}), so the abstract
 * {@code BluetoothGattCallback} must be wrapped by a real Java class.</p>
 */
public interface GpsGattCallback {

    /**
     * Called when the GATT connection state changes.
     *
     * @param status   GATT status code ({@code 0} == success).
     * @param newState one of {@code android.bluetooth.BluetoothProfile.STATE_*}
     *                 (0 disconnected, 1 connecting, 2 connected, 3 disconnecting).
     */
    void onConnectionStateChange(int status, int newState);

    /**
     * Called when service discovery finishes.
     *
     * @param status GATT status code ({@code 0} == success).
     */
    void onServicesDiscovered(int status);

    /**
     * Called when a characteristic we are subscribed to changes (a BLE
     * notification arrives).
     *
     * @param uuid  the characteristic UUID (string form).
     * @param value the notified bytes.
     */
    void onCharacteristicChanged(String uuid, byte[] value);
}
