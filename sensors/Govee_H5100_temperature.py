from .govee_utils import (
    decode_h5100_manufacturer_data,
    ensure_shared_bleak_scanner_running,
    get_shared_bleak_scanner,
    register_shared_detection_callback,
    stop_shared_bleak_scanner,
)


class GoveeH5100Temperature:
    """
    A class to interface with the Govee H5100 BLE temperature sensor via advertisements.
    """

    def __init__(self, config: dict):
        self.config = dict(config)
        self.id = self.config.get("id")
        self.identifier = self.config.get("identifier") or self.id  # Sensor address or name
        self.normalized_identifier = self._normalize_identifier(self.identifier)
        self.refresh_rate = self.config.get("refresh_rate", 30)  # Refresh rate in seconds
        self.current_temperature = None  # Last retrieved temperature value
        self.current_humidity = None  # Last retrieved humidity value
        self.battery_level = None  # Last retrieved battery level
        self.logger = None
        self.scanner = None

    async def initialize(self, spriggler_logger):
        """Initialize the sensor with the Spriggler logging system."""
        self.logger = spriggler_logger.bind(COMPONENT_TYPE="sensor", ENTITY_NAME=self.identifier)
        self.logger.info(
            "Govee5100Temperature sensor initialized.",
            config=self.config,
            normalized_identifier=self.normalized_identifier,
        )
        await self.start_scanning()

    def handle_advertisement(self, device, advertisement_data):
        """Process BLE advertisement data to extract sensor information."""
        self.logger.debug(
            "Advertisement callback invoked",
            device_address=getattr(device, "address", None),
            device_name=getattr(device, "name", None),
            manufacturer_data_present=bool(advertisement_data.manufacturer_data),
            advertisement_name=getattr(advertisement_data, "local_name", None),
        )

        device_address = getattr(device, "address", None)
        device_name = getattr(device, "name", None)
        advertisement_name = getattr(advertisement_data, "local_name", None)

        expected_signature = f"GVH5100_{self.identifier}" if self.identifier else None
        if expected_signature:
            if not any(
                expected_signature.lower() in (value or "").lower()
                for value in (advertisement_name, device_name)
            ):
                self.logger.debug(
                    "Advertisement did not match expected signature; skipping",
                    expected_signature=expected_signature,
                    advertisement_name=advertisement_name,
                    device_name=device_name,
                )
                return

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

        manufacturer_data = None
        if advertisement_data.manufacturer_data:
            manufacturer_values = list(advertisement_data.manufacturer_data.values())
            if manufacturer_values:
                manufacturer_data = manufacturer_values[0]

        if not manufacturer_data:
            self.logger.debug(
                "No manufacturer payload available; skipping",
                available_ids=list(advertisement_data.manufacturer_data.keys())
                if advertisement_data.manufacturer_data
                else [],
            )
            return

        self.logger.debug(
            "Advertisement received",
            device_address=device_address,
            device_name=device_name,
            manufacturer_data=manufacturer_data.hex() if hasattr(manufacturer_data, "hex") else manufacturer_data,
            manufacturer_data_length=len(manufacturer_data) if manufacturer_data is not None else 0,
        )

        data = self.decode_manufacturer_data(manufacturer_data)
        if data:
            self.logger.debug("Decoded manufacturer data", decoded_payload=data)
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
            self.scanner = get_shared_bleak_scanner()
            self.logger.debug(
                "Registering advertisement callback and acquiring shared BLE scanner",
                scanner_id=id(self.scanner),
            )
            register_shared_detection_callback(self.handle_advertisement, logger=self.logger)
        else:
            self.logger.debug(
                "Reusing existing shared BLE scanner for temperature sensor", scanner_id=id(self.scanner)
            )
        await ensure_shared_bleak_scanner_running(self.logger)

    async def stop_scanning(self):
        """Stop BLE scanning."""
        if self.scanner:
            self.logger.debug("Stopping BLE scanning for temperature sensor", scanner_id=id(self.scanner))
            await stop_shared_bleak_scanner(self.logger)

    async def read(self):
        """Retrieve the most recent temperature and humidity values."""
        if self.current_temperature is None or self.current_humidity is None:
            self.logger.warning("No sensor data available yet.")
            return {"error": "No sensor data available"}
        self.logger.debug(
            "Returning latest sensor readings",
            temperature_f=self.current_temperature,
            humidity_percent=self.current_humidity,
            battery_percent=self.battery_level,
        )
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

    @staticmethod
    def _normalize_identifier(value):
        if value is None:
            return None
        return value.lower().replace(":", "").replace("-", "").replace("_", "")
