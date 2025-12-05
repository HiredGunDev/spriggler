"""Govee H5100 temperature sensor."""

from typing import Any, Dict

from .govee_utils import GoveeH5100Base


class GoveeH5100Temperature(GoveeH5100Base):
    """Govee H5100 BLE sensor exposing temperature readings."""

    def _extract_reading(self, decoded: Dict[str, Any]) -> Dict[str, Any]:
        """Extract temperature and battery from decoded data."""
        return {
            "temperature": decoded.get("temperature"),
            "humidity": decoded.get("humidity"),  # Include for cross-reference
            "battery": decoded.get("battery"),
        }

    def _format_log_message(self, reading: Dict[str, Any], suppressed: int) -> str:
        """Format temperature reading log message."""
        temp = reading.get("temperature")
        humidity = reading.get("humidity")
        battery = reading.get("battery")
        return (
            f"Temperature: {temp:.2f}°F, "
            f"Humidity: {humidity:.2f}%, "
            f"Battery: {battery}% "
            f"(suppressed {suppressed} identical advertisements)"
        )

    def _get_sensor_type(self) -> str:
        return "temperature_sensor"

    def _get_protocol_name(self) -> str:
        return "Govee_H5100_temperature"
