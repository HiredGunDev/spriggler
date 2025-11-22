import asyncio
from types import SimpleNamespace

import pytest

from sensors.Govee_H5100_humidity import GoveeH5100Humidity
from sensors.Govee_H5100_temperature import GoveeH5100Temperature
from sensors import govee_utils
from sensors.govee_utils import decode_h5100_manufacturer_data


class DummyLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return self

    def info(self, message, *args, **kwargs):
        self.messages.append(("info", message, kwargs))

    def warning(self, message, *args, **kwargs):
        self.messages.append(("warning", message, kwargs))

    def debug(self, message, *args, **kwargs):
        self.messages.append(("debug", message, kwargs))


def _reset_shared_scanner_state():
    govee_utils._shared_bleak_scanner = None
    govee_utils._shared_bleak_scanner_started = False
    govee_utils._detection_callbacks.clear()


def test_decode_h5100_payload_with_battery():
    manufacturer_data = bytes([0x01, 0x02, 0x03, 0x94, 0x47, 0x00, 0x55])

    result = decode_h5100_manufacturer_data(manufacturer_data)

    assert result == {
        "temperature": pytest.approx(23.4),
        "humidity": pytest.approx(56.7),
        "battery": 0x55,
    }


def test_decode_h5100_payload_without_battery():
    manufacturer_data = bytes([0x01, 0x02, 0x03, 0x94, 0x47, 0x00])

    result = decode_h5100_manufacturer_data(manufacturer_data)

    assert result == {
        "temperature": pytest.approx(23.4),
        "humidity": pytest.approx(56.7),
        "battery": None,
    }


def test_temperature_read_uses_latest_advertisement():
    manufacturer_data = bytes([0x01, 0x02, 0x03, 0x94, 0x47, 0x00, 0x55])
    sensor = GoveeH5100Temperature({"id": "temp-1"})
    sensor.logger = DummyLogger()
    advertisement = SimpleNamespace(
        manufacturer_data={sensor.BLE_MANUFACTURER_ID: manufacturer_data}
    )
    sensor.handle_advertisement(device=None, advertisement_data=advertisement)

    reading = asyncio.run(sensor.read())

    assert reading["temperature"] == pytest.approx(74.12, rel=1e-3)
    assert reading["humidity"] == pytest.approx(56.7)
    assert reading["battery"] == 0x55


def test_temperature_handler_prefers_expected_manufacturer_data():
    correct_manufacturer_data = bytes([0x01, 0x02, 0x03, 0x94, 0x47, 0x00, 0x55])
    sensor = GoveeH5100Temperature({"id": "temp-2"})
    sensor.logger = DummyLogger()
    advertisement = SimpleNamespace(
        manufacturer_data={
            0x9999: bytes([0x00] * 6),
            sensor.BLE_MANUFACTURER_ID: correct_manufacturer_data,
        }
    )

    sensor.handle_advertisement(device=None, advertisement_data=advertisement)

    assert sensor.current_temperature == pytest.approx(74.12, rel=1e-3)
    assert sensor.current_humidity == pytest.approx(56.7)
    assert sensor.battery_level == 0x55


def test_humidity_read_handles_missing_battery():
    manufacturer_data = bytes([0x01, 0x02, 0x03, 0x94, 0x47, 0x00])
    sensor = GoveeH5100Humidity({"id": "humidity-1"})
    sensor.logger = DummyLogger()
    advertisement = SimpleNamespace(
        manufacturer_data={sensor.BLE_MANUFACTURER_ID: manufacturer_data}
    )
    sensor.handle_advertisement(device=None, advertisement_data=advertisement)

    reading = asyncio.run(sensor.read())

    assert reading["humidity"] == pytest.approx(56.7)
    assert reading["battery"] is None


def test_humidity_handler_prefers_expected_manufacturer_data():
    correct_manufacturer_data = bytes([0x01, 0x02, 0x03, 0x94, 0x47, 0x00, 0x64])
    sensor = GoveeH5100Humidity({"id": "humidity-2"})
    sensor.logger = DummyLogger()
    advertisement = SimpleNamespace(
        manufacturer_data={
            0x1234: bytes([0xFF] * 6),
            sensor.BLE_MANUFACTURER_ID: correct_manufacturer_data,
        }
    )

    sensor.handle_advertisement(device=None, advertisement_data=advertisement)

    assert sensor.current_humidity == pytest.approx(56.7)
    assert sensor.battery_level == 0x64


def test_shared_scanner_dispatches_to_all_callbacks(monkeypatch):
    callbacks = []

    class DummyScanner:
        def __init__(self, detection_callback=None):
            self.detection_callback = detection_callback

        def register_detection_callback(self, callback):
            self.detection_callback = callback

        async def start(self):
            return None

        async def stop(self):
            return None

    monkeypatch.setattr(govee_utils, "BleakScanner", DummyScanner)
    _reset_shared_scanner_state()

    def cb1(*_):
        callbacks.append("cb1")

    def cb2(*_):
        callbacks.append("cb2")

    govee_utils.register_shared_detection_callback(cb1)
    govee_utils.register_shared_detection_callback(cb2)

    govee_utils._dispatch_detection(None, SimpleNamespace())

    assert callbacks == ["cb1", "cb2"]


def test_shared_scanner_uses_manufacturer_filter_when_supported(monkeypatch):
    class DummyScanner:
        def __init__(self, *, detection_callback=None, scanning_filter=None):
            self.detection_callback = detection_callback
            self.scanning_filter = scanning_filter

    monkeypatch.setattr(govee_utils, "BleakScanner", DummyScanner)
    _reset_shared_scanner_state()

    scanner = govee_utils.get_shared_bleak_scanner()

    assert scanner.scanning_filter == {
        "ManufacturerData": [govee_utils.GOVEE_H5100_MANUFACTURER_ID]
    }
    assert scanner.detection_callback is govee_utils._dispatch_detection


def test_shared_scanner_falls_back_when_filter_unsupported(monkeypatch):
    class RejectingScanner:
        def __init__(self, *, detection_callback=None, scanning_filter=None):
            if scanning_filter is not None:
                raise TypeError("scanning_filter unsupported")
            self.detection_callback = detection_callback
            self.scanning_filter = scanning_filter

    monkeypatch.setattr(govee_utils, "BleakScanner", RejectingScanner)
    _reset_shared_scanner_state()

    scanner = govee_utils.get_shared_bleak_scanner()

    assert scanner.scanning_filter is None
    assert scanner.detection_callback is govee_utils._dispatch_detection


def test_temperature_logging_only_on_change():
    first_payload = bytes([0x01, 0x02, 0x03, 0x94, 0x47, 0x00, 0x55])
    next_payload = bytes([0x01, 0x02, 0x03, 0x94, 0x48, 0x00, 0x55])

    sensor = GoveeH5100Temperature({"id": "temp-logs"})
    sensor.logger = DummyLogger()

    advertisement = SimpleNamespace(
        manufacturer_data={sensor.BLE_MANUFACTURER_ID: first_payload}
    )
    sensor.handle_advertisement(device=None, advertisement_data=advertisement)
    sensor.handle_advertisement(device=None, advertisement_data=advertisement)

    advertisement_next = SimpleNamespace(
        manufacturer_data={sensor.BLE_MANUFACTURER_ID: next_payload}
    )
    sensor.handle_advertisement(device=None, advertisement_data=advertisement_next)

    info_messages = [message for level, message, _ in sensor.logger.messages if level == "info"]

    assert len(info_messages) == 2
    assert "suppressed 0" in info_messages[0]
    assert "suppressed 1" in info_messages[1]
    assert sensor.current_humidity == pytest.approx(56.8)


def test_humidity_logging_only_on_change():
    first_payload = bytes([0x01, 0x02, 0x03, 0x94, 0x47, 0x00])
    next_payload = bytes([0x01, 0x02, 0x03, 0x94, 0x48, 0x00])

    sensor = GoveeH5100Humidity({"id": "humidity-logs"})
    sensor.logger = DummyLogger()

    advertisement = SimpleNamespace(
        manufacturer_data={sensor.BLE_MANUFACTURER_ID: first_payload}
    )
    sensor.handle_advertisement(device=None, advertisement_data=advertisement)
    sensor.handle_advertisement(device=None, advertisement_data=advertisement)

    advertisement_next = SimpleNamespace(
        manufacturer_data={sensor.BLE_MANUFACTURER_ID: next_payload}
    )
    sensor.handle_advertisement(device=None, advertisement_data=advertisement_next)

    info_messages = [message for level, message, _ in sensor.logger.messages if level == "info"]

    assert len(info_messages) == 2
    assert "suppressed 0" in info_messages[0]
    assert "suppressed 1" in info_messages[1]
    assert sensor.current_humidity == pytest.approx(56.8)
