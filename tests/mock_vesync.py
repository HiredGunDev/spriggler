"""Mock VeSync objects for testing.

Provides a mock humidifier device and a mock manager that can be
patched into tests without network access.
"""


class MockVeSyncHumidifier:
    """Simulates a pyvesync humidifier device."""

    def __init__(self, name='Dual 200S', device_type='LUH-D301S-WUS'):
        self.device_name = name
        self.device_type = device_type
        self.device_status = 'off'
        self._mist_level = 0
        self._mode = 'manual'

    class state:
        mist_virtual_level = 0

    @property
    def is_on(self):
        return self.device_status == 'on'

    async def turn_on(self):
        self.device_status = 'on'

    async def turn_off(self):
        self.device_status = 'off'
        self._mist_level = 0
        MockVeSyncHumidifier.state.mist_virtual_level = 0

    async def set_mode(self, mode):
        self._mode = mode

    async def set_mist_level(self, level):
        self._mist_level = level
        MockVeSyncHumidifier.state.mist_virtual_level = level

    async def update(self):
        pass


class MockVeSyncManager:
    """Mock VeSync connection manager for unit tests.

    Implements the same interface as VeSyncConnectionManager but
    operates synchronously in-memory.
    """

    def __init__(self):
        self._humidifiers = {}  # name -> MockVeSyncHumidifier
        self._started = True

    def add_humidifier(self, name='Dual 200S'):
        h = MockVeSyncHumidifier(name=name)
        self._humidifiers[name] = h
        return h

    def get_humidifier(self, name):
        from spriggler.devices.vesync import VeSyncError
        if name not in self._humidifiers:
            raise VeSyncError(f"Humidifier '{name}' not found")
        return self._humidifiers[name]

    def turn_on_device(self, device):
        device.device_status = 'on'
        return True

    def turn_off_device(self, device):
        device.device_status = 'off'
        device._mist_level = 0
        type(device).state.mist_virtual_level = 0
        return True

    def set_mist_level(self, device, level):
        device._mist_level = level
        type(device).state.mist_virtual_level = level
        return True

    def update_device(self, device):
        return True

    def is_device_on(self, device):
        return device.is_on

    def get_mist_level(self, device):
        return device._mist_level

    def start(self):
        self._started = True

    def stop(self):
        self._started = False