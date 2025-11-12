import asyncio
from types import SimpleNamespace

import pytest

from sensors.Govee_H5100_humidity import GoveeH5100Humidity
from sensors.Govee_H5100_temperature import GoveeH5100Temperature
from sensors.govee_utils import decode_h5100_manufacturer_data


class DummyLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return self

    def info(self, message, *args, **kwargs):
        self.messages.append(("info", message))

    def warning(self, message, *args, **kwargs):
        self.messages.append(("warning", message))


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
    sensor = GoveeH5100Temperature(identifier="temp-1")
    logger = DummyLogger()

    asyncio.run(sensor.initialize(logger))
    advertisement = SimpleNamespace(
        manufacturer_data={sensor.BLE_MANUFACTURER_ID: manufacturer_data}
    )
    sensor.handle_advertisement(device=None, advertisement_data=advertisement)

    reading = asyncio.run(sensor.read())

    assert reading["temperature"] == pytest.approx(74.12, rel=1e-3)
    assert reading["humidity"] == pytest.approx(56.7)
    assert reading["battery"] == 0x55


def test_humidity_read_handles_missing_battery():
    manufacturer_data = bytes([0x01, 0x02, 0x03, 0x94, 0x47, 0x00])
    sensor = GoveeH5100Humidity(identifier="humidity-1")
    logger = DummyLogger()

    asyncio.run(sensor.initialize(logger))
    advertisement = SimpleNamespace(
        manufacturer_data={sensor.BLE_MANUFACTURER_ID: manufacturer_data}
    )
    sensor.handle_advertisement(device=None, advertisement_data=advertisement)

    reading = asyncio.run(sensor.read())

    assert reading["humidity"] == pytest.approx(56.7)
    assert reading["battery"] is None
