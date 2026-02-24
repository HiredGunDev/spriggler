"""Conformance tests for the Govee BLE sensor driver.

These tests validate the driver against the SensorDriver contract
WITHOUT requiring actual BLE hardware. They work by injecting
synthetic advertisement data directly into the driver's callback.
"""

import time
import pytest

from govee_ble import GoveeBluetoothDeviceData, SensorDeviceClass
from habluetooth.models import BluetoothServiceInfo

from spriggler.sensors.govee import GoveeSensor, _is_gateway
from spriggler.sensors.base import SensorDriver
from tests.conformance import SensorConformanceTests


# ── Synthetic advertisement data ─────────────────────────────────────

# H5100 advertisement: 22.3°C, 57.2% humidity, battery 76%
# Encoding: data[2:6] → decode_temp_humid_battery_error
#   temp_humid bytes[0:3]: (int(temp*10) * 1000 + int(humi*10)) as 3 big-endian bytes
#   22.3°C, 57.2% → 223*1000 + 572 = 223572 = 0x036954
#   battery byte: 76 = 0x4C (bit 7 clear = no error)
# H5100 uses 8-byte manufacturer data (6-byte hits H5072 branch first)
H5100_MFR_ID = 60552
H5100_MFR_DATA = bytes([0x00, 0x01, 0x03, 0x69, 0x54, 0x4C, 0x00, 0x00])

FAKE_MAC = "A4:C1:38:AA:BB:CC"
FAKE_SUFFIX = "BBCC"


def make_service_info(
        address: str = FAKE_MAC,
        name: str = "GVH5100_BBCC",
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


# ── Helper to create drivers without starting the real scanner ───────

def _patch_register():
    """Monkey-patch _register to skip scanner startup for testing."""
    original = GoveeSensor._register

    def fake_register(cls, inst):
        if inst._match_mode == 'mac':
            cls._mac_instances[inst._address] = inst
        else:
            cls._suffix_instances[inst._address] = inst

    GoveeSensor._register = classmethod(fake_register)
    return original


def _cleanup(original_register):
    GoveeSensor._register = original_register
    GoveeSensor._mac_instances.clear()
    GoveeSensor._suffix_instances.clear()
    GoveeSensor._scanner_running = False


# ── Verify our synthetic data actually parses ────────────────────────

class TestSyntheticDataValidity:
    """Ensure our test data is valid before testing the driver with it."""

    def test_govee_ble_recognizes_h5100(self):
        parser = GoveeBluetoothDeviceData()
        info = make_service_info()
        assert parser.supported(info)

    def test_govee_ble_parses_temperature(self):
        parser = GoveeBluetoothDeviceData()
        info = make_service_info()
        update = parser.update(info)

        found_temp = False
        for key, value in update.entity_values.items():
            desc = update.entity_descriptions.get(key)
            if desc and desc.device_class == SensorDeviceClass.TEMPERATURE:
                found_temp = True
                temp_c = float(value.native_value)
                assert -40 < temp_c < 80, f"Parsed temp {temp_c}°C seems wrong"
        assert found_temp, "No temperature found in parsed data"

    def test_govee_ble_parses_humidity(self):
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


# ── Gateway filtering ────────────────────────────────────────────────

class TestGatewayFiltering:
    def test_h5151_is_gateway(self):
        assert _is_gateway("Govee_H5151_401E")

    def test_h5100_is_not_gateway(self):
        assert not _is_gateway("GVH5100_2C6A")

    def test_empty_name_is_not_gateway(self):
        assert not _is_gateway("")


# ── Driver unit tests (MAC matching) ─────────────────────────────────

class TestGoveeSensorMAC:
    """Test the GoveeSensor driver with MAC address matching."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        original = _patch_register()
        yield
        _cleanup(original)

    @pytest.fixture
    def driver(self):
        return GoveeSensor({
            'address': FAKE_MAC,
            'scan_timeout': 120,
        })

    def test_is_sensor_driver(self, driver):
        assert isinstance(driver, SensorDriver)

    def test_driver_name(self, driver):
        assert driver.driver_name == "govee_ble"

    def test_match_mode_is_mac(self, driver):
        assert driver._match_mode == 'mac'

    def test_read_before_any_advertisement(self, driver):
        assert driver.read() is None

    def test_read_after_advertisement(self, driver):
        info = make_service_info()
        driver._on_advertisement(info)
        reading = driver.read()
        assert reading is not None
        assert 'temperature' in reading
        assert 'humidity' in reading

    def test_temperature_is_kelvin(self, driver):
        info = make_service_info()
        driver._on_advertisement(info)
        reading = driver.read()
        temp = reading['temperature']
        assert 250 < temp < 330

    def test_humidity_is_percent(self, driver):
        info = make_service_info()
        driver._on_advertisement(info)
        reading = driver.read()
        hum = reading['humidity']
        assert 0 <= hum <= 100

    def test_rssi_included(self, driver):
        info = make_service_info(rssi=-72)
        driver._on_advertisement(info)
        reading = driver.read()
        assert reading['signal_strength'] == -72

    def test_reading_is_copy(self, driver):
        info = make_service_info()
        driver._on_advertisement(info)
        r1 = driver.read()
        r2 = driver.read()
        assert r1 is not r2
        assert r1 == r2

    def test_stale_reading_returns_none(self, driver):
        info = make_service_info()
        driver._on_advertisement(info)
        driver._last_reading_time = time.time() - 200
        assert driver.read() is None

    def test_new_advertisement_replaces_old(self, driver):
        info1 = make_service_info(rssi=-70)
        driver._on_advertisement(info1)
        info2 = make_service_info(rssi=-80)
        driver._on_advertisement(info2)
        r = driver.read()
        assert r['signal_strength'] == -80


# ── Driver unit tests (suffix matching) ──────────────────────────────

class TestGoveeSensorSuffix:
    """Test the GoveeSensor driver with name suffix matching."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        original = _patch_register()
        yield
        _cleanup(original)

    @pytest.fixture
    def driver(self):
        return GoveeSensor({
            'address': FAKE_SUFFIX,
            'scan_timeout': 120,
        })

    def test_match_mode_is_suffix(self, driver):
        assert driver._match_mode == 'suffix'

    def test_registered_in_suffix_instances(self, driver):
        assert FAKE_SUFFIX in GoveeSensor._suffix_instances

    def test_read_after_advertisement(self, driver):
        info = make_service_info(
            address="SOME-MACOS-UUID-HERE",
            name="GVH5100_BBCC",
        )
        driver._on_advertisement(info)
        reading = driver.read()
        assert reading is not None
        assert 'temperature' in reading

    def test_find_instance_by_suffix(self):
        d = GoveeSensor({'address': '2C6A', 'scan_timeout': 120})
        found = GoveeSensor._find_instance(
            "RANDOM-UUID", "GVH5100_2C6A"
        )
        assert found is d

    def test_find_instance_by_mac(self):
        d = GoveeSensor({'address': FAKE_MAC, 'scan_timeout': 120})
        found = GoveeSensor._find_instance(FAKE_MAC, "GVH5100_BBCC")
        assert found is d

    def test_find_instance_unknown_returns_none(self):
        GoveeSensor({'address': '2C6A', 'scan_timeout': 120})
        found = GoveeSensor._find_instance(
            "RANDOM-UUID", "GVH5100_9999"
        )
        assert found is None


# ── Config validation tests ──────────────────────────────────────────

class TestGoveeConfigValidation:

    def test_missing_address_raises(self):
        d = GoveeSensor.__new__(GoveeSensor)
        with pytest.raises(ValueError, match="address"):
            d.validate_config({})

    def test_invalid_address_raises(self):
        d = GoveeSensor.__new__(GoveeSensor)
        with pytest.raises(ValueError, match="Invalid"):
            d.validate_config({'address': 'not-valid!'})

    def test_valid_mac_passes(self):
        d = GoveeSensor.__new__(GoveeSensor)
        d.validate_config({'address': 'A4:C1:38:AA:BB:CC'})

    def test_lowercase_mac_passes(self):
        d = GoveeSensor.__new__(GoveeSensor)
        d.validate_config({'address': 'a4:c1:38:aa:bb:cc'})

    def test_valid_suffix_passes(self):
        d = GoveeSensor.__new__(GoveeSensor)
        d.validate_config({'address': '2C6A'})

    def test_lowercase_suffix_passes(self):
        d = GoveeSensor.__new__(GoveeSensor)
        d.validate_config({'address': '2c6a'})


# ── Conformance harness ──────────────────────────────────────────────

class TestGoveeConformance(SensorConformanceTests):
    """Run the standard sensor conformance tests against GoveeSensor."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        original = _patch_register()
        yield
        _cleanup(original)

    @pytest.fixture
    def driver(self):
        d = GoveeSensor({
            'address': FAKE_MAC,
            'scan_timeout': 120,
        })
        info = make_service_info()
        d._on_advertisement(info)
        return d

    @pytest.fixture
    def sample_reading(self):
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
        return {'address': 'not-valid!'}