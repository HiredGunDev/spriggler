"""Govee H5100 humidity sensor."""

from typing import Any, Dict

from .govee_utils import GoveeH5100Base


class GoveeH5100Humidity(GoveeH5100Base):
    """Govee H5100 BLE sensor exposing humidity readings."""

    def _extract_reading(self, decoded: Dict[str, Any]) -> Dict[str, Any]:
        """Extract humidity and battery from decoded data."""
        return {
            "humidity": decoded.get("humidity"),
            "battery": decoded.get("battery"),
        }

    def _format_log_message(self, reading: Dict[str, Any], suppressed: int) -> str:
        """Format humidity reading log message."""
        humidity = reading.get("humidity")
        battery = reading.get("battery")
        battery_str = f"{battery}%" if battery is not None else "N/A"
        return (
            f"Humidity: {humidity:.2f}%, "
            f"Battery: {battery_str} "
            f"(suppressed {suppressed} identical advertisements)"
        )

    def _get_sensor_type(self) -> str:
        return "humidity_sensor"

    def _get_protocol_name(self) -> str:
        return "Govee_H5100_humidity"
