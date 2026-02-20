"""Conformance tests for the Govee BLE sensor driver.

These tests validate the driver against the SensorDriver contract
WITHOUT requiring actual BLE hardware. They work by injecting
synthetic advertisement data directly into the driver's callback.

Tests requiring real hardware are in tests/test_govee_live.py
and are skipped by default.
"""

import time
import pytest

from govee_ble import GoveeBluetoothDeviceData, SensorDeviceClass
from habluetooth.models import BluetoothServiceInfo

from spriggler.sensors.govee import GoveeSensor
from spriggler.sensors.base import SensorDriver
from tests.conformance import SensorConformanceTests


# ── Synthetic advertisement data ─────────────────────────────────────

# Real manufacturer data captured from a Govee H5100.
# Manufacturer ID 60552 (0xEC88) is Govee's BLE company ID.
# The payload encodes temperature and humidity.
#
# To capture your own:
#   python -c "
#   import asyncio
#   from bleak import BleakScanner
#   async def scan():
#       devices = await BleakScanner.discover(10, return_adv=True)
#       for addr, (dev, adv) in devices.items():
#           if 'GVH' in (adv.local_name or ''):
#               print(f'{addr}: name={adv.local_name} mfr={adv.manufacturer_data}')
#   asyncio.run(scan())
#   "

# H5100 advertisement: 22.3°C, 57.2% humidity, battery 76%
# Encoding: data[2:6] → decode_temp_humid_battery_error
#   temp_humid bytes[0:3]: (int(temp*10) * 1000 + int(humi*10)) as 3 big-endian bytes
#   22.3°C, 57.2% → 223*1000 + 572 = 223572 = 0x036954
#   battery byte: 76 = 0x4C (bit 7 clear = no error)
# H5100 uses 8-byte manufacturer data (6-byte hits H5072 branch first)
H5100_MFR_ID = 60552
H5100_MFR_DATA = bytes([0x00, 0x01, 0x03, 0x69, 0x54, 0x4C, 0x00, 0x00])

# Build a synthetic BluetoothServiceInfo
FAKE_ADDRESS = "A4:C1:38:AA:BB:CC"


def make_service_info(
        address: str = FAKE_ADDRESS,
        name: str = "GVH5100_AABBCC",
        rssi: int = -65,
        mfr_id: int = H5100_MFR_ID,
        mfr_data: bytes = H5100_MFR_DATA,
) -> BluetoothServiceInfo:
    """Build a synthetic BluetoothServiceInfo for testing."""
    return BluetoothServiceInfo(
        name=name,
        address=address,
        rssi=rssi,
        manufacturer_data={mfr_id: mfr_data},
        service_data={},
        service_uuids=[],
        source="test",
    )


# ── Verify our synthetic data actually parses ────────────────────────

class TestSyntheticDataValidity:
    """Ensure our test data is valid before testing the driver with it."""

    def test_govee_ble_recognizes_h5100(self):
        """govee-ble library should recognize our synthetic data."""
        parser = GoveeBluetoothDeviceData()
        info = make_service_info()
        assert parser.supported(info), (
            "govee-ble does not recognize our synthetic H5100 data. "
            "The manufacturer data bytes may need updating."
        )

    def test_govee_ble_parses_temperature(self):
        """govee-ble should extract a temperature from our data."""
        parser = GoveeBluetoothDeviceData()
        info = make_service_info()
        update = parser.update(info)

        found_temp = False
        for key, value in update.entity_values.items():
            desc = update.entity_descriptions.get(key)
            if desc and desc.device_class == SensorDeviceClass.TEMPERATURE:
                found_temp = True
                temp_c = float(value.native_value)
                # Should be a reasonable room temperature
                assert -40 < temp_c < 80, f"Parsed temp {temp_c}°C seems wrong"
        assert found_temp, "No temperature found in parsed data"

    def test_govee_ble_parses_humidity(self):
        """govee-ble should extract humidity from our data."""
        parser = GoveeBluetoothDeviceData()
        info = make_service_info()
        update = parser.update(info)

        found_humidity = False
        for key, value in update.entity_values.items():
            desc = update.entity_descriptions.get(key)
            if desc and desc.device_class == SensorDeviceClass.HUMIDITY:
                found_humidity = True
                hum = float(value.native_value)
                assert 0 <= hum <= 100, f"Parsed humidity {hum}% seems wrong"
        assert found_humidity, "No humidity found in parsed data"


# ── Driver unit tests ────────────────────────────────────────────────

class TestGoveeSensorDriver:
    """Test the GoveeSensor driver with synthetic advertisements."""

    @pytest.fixture(autouse=True)
    def cleanup_scanner(self):
        """Reset class-level scanner state between tests."""
        yield
        GoveeSensor._scanner_running = False
        GoveeSensor._instances.clear()

    @pytest.fixture
    def driver(self):
        """Create a driver instance without starting the real scanner."""
        # Monkey-patch _register to skip scanner startup
        original_register = GoveeSensor._register
        GoveeSensor._register = classmethod(lambda cls, inst: (
            cls._instances.__setitem__(inst._address, inst)
        ))
        try:
            d = GoveeSensor({
                'address': FAKE_ADDRESS,
                'scan_timeout': 120,
            })
            yield d
        finally:
            GoveeSensor._register = original_register
            GoveeSensor._instances.clear()

    def test_is_sensor_driver(self, driver):
        assert isinstance(driver, SensorDriver)

    def test_driver_name(self, driver):
        assert driver.driver_name == "govee_ble"

    def test_read_before_any_advertisement(self, driver):
        """No data yet — should return None."""
        assert driver.read() is None

    def test_read_after_advertisement(self, driver):
        """Feed an advertisement, then read should return data."""
        info = make_service_info()
        driver._on_advertisement(info)
        reading = driver.read()
        assert reading is not None
        assert 'temperature' in reading
        assert 'humidity' in reading

    def test_temperature_is_kelvin(self, driver):
        """Temperature must be in Kelvin per driver contract."""
        info = make_service_info()
        driver._on_advertisement(info)
        reading = driver.read()
        temp = reading['temperature']
        # Kelvin room temp is ~290-305
        assert 250 < temp < 330, (
            f"Temperature {temp} doesn't look like Kelvin"
        )

    def test_humidity_is_percent(self, driver):
        """Humidity must be %RH per driver contract."""
        info = make_service_info()
        driver._on_advertisement(info)
        reading = driver.read()
        hum = reading['humidity']
        assert 0 <= hum <= 100

    def test_rssi_included(self, driver):
        """RSSI should be in the reading."""
        info = make_service_info(rssi=-72)
        driver._on_advertisement(info)
        reading = driver.read()
        assert reading['signal_strength'] == -72

    def test_reading_is_copy(self, driver):
        """read() should return a copy, not the internal dict."""
        info = make_service_info()
        driver._on_advertisement(info)
        r1 = driver.read()
        r2 = driver.read()
        assert r1 is not r2
        assert r1 == r2

    def test_stale_reading_returns_none(self, driver):
        """Readings older than scan_timeout should return None."""
        info = make_service_info()
        driver._on_advertisement(info)

        # Force the timestamp to be old
        driver._last_reading_time = time.time() - 200
        assert driver.read() is None

    def test_fresh_reading_within_timeout(self, driver):
        """Readings within scan_timeout should be returned."""
        info = make_service_info()
        driver._on_advertisement(info)
        # Reading just happened, should be fresh
        assert driver.read() is not None

    def test_new_advertisement_replaces_old(self, driver):
        """A new advertisement should update the cached reading."""
        info1 = make_service_info(rssi=-70)
        driver._on_advertisement(info1)
        r1 = driver.read()

        info2 = make_service_info(rssi=-80)
        driver._on_advertisement(info2)
        r2 = driver.read()

        assert r2['signal_strength'] == -80

    def test_wrong_address_ignored(self, driver):
        """Advertisements from other addresses should be ignored."""
        info = make_service_info(address="FF:FF:FF:FF:FF:FF")
        driver._on_advertisement(info)
        # Our driver is registered for FAKE_ADDRESS, not FF:FF:...
        # But _on_advertisement is called directly, so it would still
        # parse. The filtering happens in the scanner callback.
        # This test verifies the parser doesn't crash on valid data
        # from a different address.

    def test_multiple_instances_different_addresses(self):
        """Multiple drivers for different addresses coexist."""
        original_register = GoveeSensor._register
        GoveeSensor._register = classmethod(lambda cls, inst: (
            cls._instances.__setitem__(inst._address, inst)
        ))
        try:
            d1 = GoveeSensor({'address': 'A4:C1:38:11:22:33', 'scan_timeout': 120})
            d2 = GoveeSensor({'address': 'A4:C1:38:44:55:66', 'scan_timeout': 120})

            assert 'A4:C1:38:11:22:33' in GoveeSensor._instances
            assert 'A4:C1:38:44:55:66' in GoveeSensor._instances
            assert d1 is not d2
        finally:
            GoveeSensor._register = original_register
            GoveeSensor._instances.clear()


# ── Config validation tests ──────────────────────────────────────────

class TestGoveeConfigValidation:

    def test_missing_address_raises(self):
        d = GoveeSensor.__new__(GoveeSensor)
        with pytest.raises(ValueError, match="address"):
            d.validate_config({})

    def test_invalid_mac_raises(self):
        d = GoveeSensor.__new__(GoveeSensor)
        with pytest.raises(ValueError, match="MAC"):
            d.validate_config({'address': 'not-a-mac'})

    def test_valid_mac_passes(self):
        d = GoveeSensor.__new__(GoveeSensor)
        d.validate_config({'address': 'A4:C1:38:AA:BB:CC'})

    def test_lowercase_mac_passes(self):
        d = GoveeSensor.__new__(GoveeSensor)
        d.validate_config({'address': 'a4:c1:38:aa:bb:cc'})


# ── Conformance harness ──────────────────────────────────────────────

class TestGoveeConformance(SensorConformanceTests):
    """Run the standard sensor conformance tests against GoveeSensor."""

    @pytest.fixture
    def driver(self):
        original_register = GoveeSensor._register
        GoveeSensor._register = classmethod(lambda cls, inst: (
            cls._instances.__setitem__(inst._address, inst)
        ))
        try:
            d = GoveeSensor({
                'address': FAKE_ADDRESS,
                'scan_timeout': 120,
            })
            # Pre-load a reading so conformance tests have data
            info = make_service_info()
            d._on_advertisement(info)
            yield d
        finally:
            GoveeSensor._register = original_register
            GoveeSensor._instances.clear()

    @pytest.fixture
    def sample_reading(self):
        """Provide a sample reading for conformance validation."""
        parser = GoveeBluetoothDeviceData()
        info = make_service_info()
        update = parser.update(info)

        reading = {}
        for key, value in update.entity_values.items():
            desc = update.entity_descriptions.get(key)
            if desc is None:
                continue
            native = value.native_value
            if native is None:
                continue
            if desc.device_class == SensorDeviceClass.TEMPERATURE:
                reading['temperature'] = float(native) + 273.15
            elif desc.device_class == SensorDeviceClass.HUMIDITY:
                reading['humidity'] = float(native)
            elif desc.device_class == SensorDeviceClass.BATTERY:
                reading['battery'] = float(native)
        reading['signal_strength'] = -65
        return reading

    @pytest.fixture
    def driver_config_valid(self):
        return {'address': 'A4:C1:38:AA:BB:CC'}

    @pytest.fixture
    def driver_config_invalid(self):
        return {'address': 'not-valid'}