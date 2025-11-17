from .govee_utils import (
    GOVEE_H5100_MANUFACTURER_ID,
    decode_h5100_manufacturer_data,
    ensure_shared_bleak_scanner_running,
    get_shared_bleak_scanner,
    register_shared_detection_callback,
    stop_shared_bleak_scanner,
)


class GoveeH5100Humidity:
    """Interface with the Govee H5100 BLE sensor for humidity readings."""

    BLE_MANUFACTURER_ID = GOVEE_H5100_MANUFACTURER_ID

    def __init__(self, config: dict):
        self.config = dict(config)
        self.id = self.config.get("id")
        self.identifier = self.config.get("identifier") or self.id
        self.normalized_identifier = self._normalize_identifier(self.identifier)
        self.refresh_rate = self.config.get("refresh_rate", 30)
        self.current_humidity = None
        self.battery_level = None
        self.logger = None
        self.scanner = None

    async def initialize(self, spriggler_logger):
        """Initialize the sensor with the Spriggler logging system."""
        self.logger = spriggler_logger.bind(COMPONENT_TYPE="sensor", ENTITY_NAME=self.identifier)
        self.logger.info("Govee5100Humidity sensor initialized.")
        await self.start_scanning()

    def handle_advertisement(self, device, advertisement_data):
        """Process BLE advertisement data to extract humidity information."""
        if not advertisement_data.manufacturer_data:
            return

        manufacturer_data = advertisement_data.manufacturer_data.get(self.BLE_MANUFACTURER_ID)
        if not manufacturer_data:
            return

        device_address = getattr(device, "address", None)
        device_name = getattr(device, "name", None)

        if self.normalized_identifier and device is not None:
            normalized_address = self._normalize_identifier(device_address)
            normalized_name = self._normalize_identifier(device_name)

            if not (
                normalized_address == self.normalized_identifier
                or normalized_name == self.normalized_identifier
            ):
                # Ignore advertisements from other Govee devices.
                self.logger.debug(
                    "Skipping advertisement due to identifier mismatch",
                    configured_identifier=self.identifier,
                    normalized_identifier=self.normalized_identifier,
                    device_address=device_address,
                    normalized_address=normalized_address,
                    device_name=device_name,
                    normalized_name=normalized_name,
                )
                return

        self.logger.debug(
            "Advertisement received",
            device_address=device_address,
            device_name=device_name,
            manufacturer_data=manufacturer_data.hex() if hasattr(manufacturer_data, "hex") else manufacturer_data,
        )

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
        if self.scanner is None:
            self.scanner = get_shared_bleak_scanner()
            register_shared_detection_callback(self.handle_advertisement)
        await ensure_shared_bleak_scanner_running(self.logger)

    async def stop_scanning(self):
        """Stop BLE scanning."""
        if self.scanner:
            await stop_shared_bleak_scanner(self.logger)

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

    @staticmethod
    def _normalize_identifier(value):
        if value is None:
            return None
        return value.lower().replace(":", "").replace("-", "").replace("_", "")
