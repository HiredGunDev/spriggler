import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import types

dummy_kasa = types.ModuleType("kasa")
dummy_kasa.Discover = types.SimpleNamespace(discover=None)
dummy_kasa.Module = types.SimpleNamespace(IotCountdown="countdown")


class _PlaceholderStrip:
    def __init__(self, *_, **__):  # pragma: no cover - replaced in tests
        raise AssertionError("SmartStrip should be monkeypatched in tests")


dummy_kasa.SmartStrip = _PlaceholderStrip
sys.modules.setdefault("kasa", dummy_kasa)

from devices import KASA_Powerbar as kasa_module  # noqa: E402


class DummyOutlet:
    def __init__(self, alias):
        self.alias = alias
        self.on_called = False
        self.off_called = False
        self.is_on = False
        self.update_calls = 0
        self.set_timer_calls = []
        self.schedule_rules = []
        self.delete_timer_calls = 0
        self.delete_schedule_calls = 0
        self.countdown_module = DummyCountdown()
        self.modules = {
            dummy_kasa.Module.IotCountdown: self.countdown_module,
            "countdown": self.countdown_module,
        }

    async def turn_on(self):  # pragma: no cover - exercised through wrapper
        await asyncio.sleep(0)
        self.on_called = True
        self.is_on = True

    async def turn_off(self):  # pragma: no cover - exercised through wrapper
        await asyncio.sleep(0)
        self.off_called = True
        self.is_on = False

    async def update(self):  # pragma: no cover - exercised through wrapper
        await asyncio.sleep(0)
        self.update_calls += 1

    async def set_timer(self, timeout_seconds, target_state):
        await asyncio.sleep(0)
        self.set_timer_calls.append((timeout_seconds, target_state))

    async def add_schedule_rule(self, rule):
        await asyncio.sleep(0)
        self.schedule_rules.append(rule)

    async def delete_timer(self):
        await asyncio.sleep(0)
        self.delete_timer_calls += 1

    async def delete_schedule_rules(self):
        await asyncio.sleep(0)
        self.delete_schedule_calls += 1


class DummyCountdown:
    def __init__(self):
        self.add_rule_calls = []
        self.delete_all_calls = 0

    async def delete_all_rules(self):
        await asyncio.sleep(0)
        self.delete_all_calls += 1

    async def call(self, command, payload=None):
        await asyncio.sleep(0)
        if command != "add_rule":
            raise AssertionError(f"Unexpected command {command}")

        self.add_rule_calls.append(payload or {})


class DummyProtocol:
    def __init__(self, port=9999):
        self.port = port


class DummyStrip:
    def __init__(self, host="192.168.1.10", port=9999, outlets=None):
        self.host = host
        self.protocol = DummyProtocol(port)
        self.port = port
        self.children = outlets or []
        self.updated = False
        self.update_calls = 0

    async def update(self):
        await asyncio.sleep(0)
        self.updated = True
        self.update_calls += 1


class DummyDiscoveredDevice:
    def __init__(self, alias):
        self.alias = alias


@pytest.fixture(autouse=True)
def reset_kasa_cache():
    kasa_module.KasaPowerbar._strip_cache.clear()
    yield
    kasa_module.KasaPowerbar._strip_cache.clear()


def test_initialize_with_discovery(monkeypatch):
    heater_outlet = DummyOutlet("Heater")

    created_instances = []

    def mock_smart_strip(host):
        strip = DummyStrip(host=host, outlets=[heater_outlet])
        created_instances.append(strip)
        return strip

    async def mock_discover():
        await asyncio.sleep(0)
        return {"192.168.1.55": DummyDiscoveredDevice("Seedling Strip")}

    monkeypatch.setattr(kasa_module, "SmartStrip", mock_smart_strip)
    monkeypatch.setattr(kasa_module.Discover, "discover", mock_discover)

    device = kasa_module.KasaPowerbar(
        {
            "id": "heater_seedling",
            "what": "heater",
            "control": {"name": "Seedling Strip", "outlet_name": "Heater"},
        }
    )

    asyncio.run(device.initialize())

    strip = created_instances[0]
    assert device.address == "192.168.1.55"
    assert strip.updated is True
    assert strip.protocol.port == kasa_module.DEFAULT_KASA_PORT
    assert device.get_metadata()["available_outlets"] == ["Heater"]

    asyncio.run(device.turn_on())
    asyncio.run(device.turn_off())

    assert heater_outlet.on_called is True
    assert heater_outlet.off_called is True


def test_initialize_with_static_ip(monkeypatch):
    fan_outlet = DummyOutlet("Fan")

    created_instances = []

    def mock_smart_strip(host):
        strip = DummyStrip(host=host, outlets=[fan_outlet])
        created_instances.append(strip)
        return strip

    async def fail_discovery():  # pragma: no cover - ensures discovery is not invoked
        raise AssertionError("Discovery should not run when ip_address is provided")

    monkeypatch.setattr(kasa_module, "SmartStrip", mock_smart_strip)
    monkeypatch.setattr(kasa_module.Discover, "discover", fail_discovery)

    device = kasa_module.KasaPowerbar(
        {
            "id": "fan_seedling",
            "what": "fan",
            "control": {
                "ip_address": "192.168.1.99",
                "port": 12345,
                "outlet_name": "Fan",
            },
        }
    )

    asyncio.run(device.initialize())

    strip = created_instances[0]
    assert strip.host == "192.168.1.99"
    assert strip.protocol.port == 12345


def test_missing_outlet_raises(monkeypatch):
    def mock_smart_strip(host, port=9999):
        return DummyStrip(host=host, port=port, outlets=[DummyOutlet("Other")])

    async def mock_discover():
        await asyncio.sleep(0)
        return {"192.168.1.20": DummyDiscoveredDevice("Seedling Strip")}

    monkeypatch.setattr(kasa_module, "SmartStrip", mock_smart_strip)
    monkeypatch.setattr(kasa_module.Discover, "discover", mock_discover)

    device = kasa_module.KasaPowerbar(
        {
            "id": "light_seedling",
            "what": "light",
            "control": {"name": "Seedling Strip", "outlet_name": "Light"},
        }
    )

    with pytest.raises(ValueError) as exc:
        asyncio.run(device.initialize())

    assert "Available outlets" in str(exc.value)


def test_reuses_cached_strip(monkeypatch):
    heater_outlet = DummyOutlet("Heater")
    light_outlet = DummyOutlet("Lights")

    created_instances = []

    def mock_smart_strip(host):
        strip = DummyStrip(host=host, outlets=[heater_outlet, light_outlet])
        created_instances.append(strip)
        return strip

    async def mock_discover():
        await asyncio.sleep(0)
        return {"192.168.1.55": DummyDiscoveredDevice("Seedling Strip")}

    monkeypatch.setattr(kasa_module, "SmartStrip", mock_smart_strip)
    monkeypatch.setattr(kasa_module.Discover, "discover", mock_discover)

    heater_device = kasa_module.KasaPowerbar(
        {
            "id": "heater_seedling",
            "what": "heater",
            "control": {"name": "Seedling Strip", "outlet_name": "Heater"},
        }
    )
    light_device = kasa_module.KasaPowerbar(
        {
            "id": "light_seedling",
            "what": "light",
            "control": {"name": "Seedling Strip", "outlet_name": "Lights"},
        }
    )

    asyncio.run(heater_device.initialize())
    asyncio.run(light_device.initialize())

    assert len(created_instances) == 1
    assert created_instances[0].update_calls == 1
    assert heater_device.address == light_device.address == "192.168.1.55"
    assert heater_device._strip is light_device._strip  # noqa: SLF001 - intentional cache check


@pytest.mark.parametrize(
    "control_block,expected_message",
    [
        ({}, "requires a 'control'"),
        ({"name": "strip"}, "requires 'control.outlet_name'"),
        ({"outlet_name": "one"}, "requires either 'control.name' or 'control.ip_address'"),
    ],
)
def test_configuration_validation(control_block, expected_message):
    with pytest.raises(ValueError) as exc:
        kasa_module.KasaPowerbar({"id": "bad", "control": control_block})

    assert expected_message in str(exc.value)


def test_outlet_specific_safety_precedence(monkeypatch):
    heater_outlet = DummyOutlet("Heater")
    fan_outlet = DummyOutlet("Fan")

    strip = DummyStrip(host="192.168.1.77", outlets=[heater_outlet, fan_outlet])

    def mock_smart_strip(host):
        assert host == "192.168.1.77"
        return strip

    monkeypatch.setattr(kasa_module, "SmartStrip", mock_smart_strip)

    control_base = {
        "ip_address": "192.168.1.77",
        "outlet_name": "Heater",
        "outlets": [
            {
                "outlet_name": "Heater",
                "safety": {
                    "target_state": "off",
                    "timeout_minutes": 5,
                    "enforce": True,
                },
            },
            {
                "outlet_name": "Fan",
                "safety": {
                    "target_state": "on",
                    "timeout_minutes": 1,
                    "enforce": False,
                },
            },
        ],
        "safety": {"target_state": "off", "timeout_minutes": 10},
    }

    heater_device = kasa_module.KasaPowerbar(
        {"id": "heater", "control": dict(control_base, outlet_name="Heater")}
    )
    fan_device = kasa_module.KasaPowerbar(
        {"id": "fan", "control": dict(control_base, outlet_name="Fan")}
    )

    asyncio.run(heater_device.initialize())
    asyncio.run(fan_device.initialize())

    asyncio.run(heater_device.turn_on())
    asyncio.run(fan_device.turn_on())

    assert heater_outlet.countdown_module.add_rule_calls == [
        {"act": 0, "delay": 300, "enable": 1, "name": "spriggler-safety"}
    ]
    assert heater_outlet.countdown_module.delete_all_calls == 1
    assert fan_outlet.countdown_module.add_rule_calls == []
    assert fan_outlet.countdown_module.delete_all_calls == 1
    assert heater_device.get_metadata()["safety"]["target_state"] == "off"
    assert fan_device.get_metadata()["safety"]["enforce"] is False


def test_default_safety_used_when_no_outlet_override(monkeypatch):
    humidifier_outlet = DummyOutlet("Humidifier")

    def mock_smart_strip(host):
        return DummyStrip(host=host, outlets=[humidifier_outlet])

    monkeypatch.setattr(kasa_module, "SmartStrip", mock_smart_strip)

    device = kasa_module.KasaPowerbar(
        {
            "id": "humidifier",
            "control": {
                "ip_address": "192.168.1.88",
                "outlet_name": "Humidifier",
                "safety": {"target_state": "on", "timeout_minutes": 0.5},
            },
        }
    )

    asyncio.run(device.initialize())
    asyncio.run(device.turn_off())

    assert humidifier_outlet.countdown_module.delete_all_calls == 1
    assert humidifier_outlet.countdown_module.add_rule_calls == [
        {"act": 1, "delay": 30, "enable": 1, "name": "spriggler-safety"}
    ]
    assert device.get_metadata()["safety"]["scope"] == "outlet"
