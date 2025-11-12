from bleak import BleakScanner

from .govee_utils import GOVEE_H5100_MANUFACTURER_ID, decode_h5100_manufacturer_data


class GoveeH5100Humidity:
    """Interface with the Govee H5100 BLE sensor for humidity readings."""

    BLE_MANUFACTURER_ID = GOVEE_H5100_MANUFACTURER_ID

    def __init__(self, identifier, refresh_rate=30):
        self.identifier = identifier
        self.refresh_rate = refresh_rate
        self.current_humidity = None
        self.battery_level = None
        self.logger = None
        self.scanner = None

    async def initialize(self, spriggler_logger):
        """Initialize the sensor with the Spriggler logging system."""
        self.logger = spriggler_logger.bind(COMPONENT_TYPE="sensor", ENTITY_NAME=self.identifier)
        self.logger.info("Govee5100Humidity sensor initialized.")

    def handle_advertisement(self, device, advertisement_data):
        """Process BLE advertisement data to extract humidity information."""
        if not advertisement_data.manufacturer_data:
            return

        manufacturer_data = advertisement_data.manufacturer_data.get(self.BLE_MANUFACTURER_ID)
        if not manufacturer_data:
            return

        data = self.decode_manufacturer_data(manufacturer_data)
        if data:
            self.current_humidity = data["humidity"]
            self.battery_level = data["battery"]
            self.logger.info(
                f"Humidity: {self.current_humidity:.2f}%, Battery: {self.battery_level if self.battery_level is not None else 'N/A'}%"
            )
        else:
            self.logger.warning("Failed to decode manufacturer data.")

    async def start_scanning(self):
        """Start scanning for BLE advertisements."""
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
        """Retrieve the most recent humidity value."""
        if self.current_humidity is None:
            self.logger.warning("No sensor data available yet.")
            return {"error": "No sensor data available"}
        return {
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
            "type": "humidity_sensor",
            "protocol": "Govee_H5100_humidity",
            "refresh_rate": self.refresh_rate,
        }
