from bleak import BleakScanner

from .govee_utils import GOVEE_H5100_MANUFACTURER_ID, decode_h5100_manufacturer_data


class GoveeH5100Temperature:
    """
    A class to interface with the Govee H5100 BLE temperature sensor via advertisements.
    """

    BLE_MANUFACTURER_ID = GOVEE_H5100_MANUFACTURER_ID  # Hardcoded Manufacturer ID for Govee H5100

    def __init__(self, config: dict):
        self.config = dict(config)
        self.id = self.config.get("id")
        self.identifier = self.config.get("identifier") or self.id  # Sensor address or name
        self.refresh_rate = self.config.get("refresh_rate", 30)  # Refresh rate in seconds
        self.current_temperature = None  # Last retrieved temperature value
        self.current_humidity = None  # Last retrieved humidity value
        self.battery_level = None  # Last retrieved battery level
        self.logger = None
        self.scanner = None

    async def initialize(self, spriggler_logger):
        """Initialize the sensor with the Spriggler logging system."""
        self.logger = spriggler_logger.bind(COMPONENT_TYPE="sensor", ENTITY_NAME=self.identifier)
        self.logger.info("Govee5100Temperature sensor initialized.")
        await self.start_scanning()

    def handle_advertisement(self, device, advertisement_data):
        """Process BLE advertisement data to extract sensor information."""
        if not advertisement_data.manufacturer_data:
            return

        manufacturer_data = advertisement_data.manufacturer_data.get(self.BLE_MANUFACTURER_ID)
        if not manufacturer_data:
            return

        if self.identifier and (
            (device.address and self.identifier not in device.address)
            and (device.name and self.identifier not in device.name)
        ):
            # Ignore advertisements from other Govee devices.
            return

        self.logger.debug(
            "Advertisement received",
            device_address=device.address,
            device_name=device.name,
            manufacturer_data=manufacturer_data.hex() if hasattr(manufacturer_data, "hex") else manufacturer_data,
        )

        data = self.decode_manufacturer_data(manufacturer_data)
        if data:
            self.current_temperature = data["temperature"] * 1.8 + 32  # Convert to Fahrenheit
            self.current_humidity = data["humidity"]
            self.battery_level = data["battery"]
            self.logger.info(
                f"Temperature: {self.current_temperature:.2f}°F, Humidity: {self.current_humidity:.2f}%, "
                f"Battery: {self.battery_level}%"
            )
        else:
            self.logger.warning("Failed to decode manufacturer data.")

    async def start_scanning(self):
        """Start scanning for BLE advertisements."""
        if self.scanner is None:
            self.scanner = BleakScanner()
            self.scanner.register_detection_callback(self.handle_advertisement)
        await self.scanner.start()
        self.logger.info("BLE scanning started.")

    async def stop_scanning(self):
        """Stop BLE scanning."""
        if self.scanner:
            await self.scanner.stop()
            self.logger.info("BLE scanning stopped.")

    async def read(self):
        """Retrieve the most recent temperature and humidity values."""
        if self.current_temperature is None or self.current_humidity is None:
            self.logger.warning("No sensor data available yet.")
            return {"error": "No sensor data available"}
        return {
            "temperature": self.current_temperature,
            "humidity": self.current_humidity,
            "battery": self.battery_level,
        }

    @staticmethod
    def decode_manufacturer_data(manufacturer_data):
        """Decode manufacturer data for GVH5100 devices."""
        return decode_h5100_manufacturer_data(manufacturer_data)

    def get_metadata(self):
        """Return metadata about the sensor."""
        return {
            "id": self.identifier,
            "type": "temperature_sensor",
            "protocol": "Govee_H5100_temperature",
            "refresh_rate": self.refresh_rate,
        }
