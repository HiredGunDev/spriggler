"""Minimal JSON Schema validator tailored for the Spriggler configuration schema."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


@dataclass
class SchemaValidationError(Exception):
    """Raised when the provided instance does not satisfy the schema."""

    path: Sequence[Any]
    message: str

    def __str__(self) -> str:  # pragma: no cover - repr helper
        return f"{self.location}: {self.message}"

    @property
    def location(self) -> str:
        if not self.path:
            return "<root>"
        return ".".join(str(part) for part in self.path)


def validate_schema(instance: Any, schema: Mapping[str, Any]) -> None:
    """Validate *instance* against *schema*.

    The validator supports the subset of JSON Schema used by
    ``docs/configuration_schema.json`` and raises :class:`SchemaValidationError`
    on failure.
    """

    _validate(instance, schema, ())


def _validate(instance: Any, schema: Mapping[str, Any], path: Sequence[Any]) -> None:
    if not isinstance(schema, Mapping):
        return

    # Draft-07 allows the "$schema" keyword, which we can safely ignore.
    if "$schema" in schema:
        schema = {k: v for k, v in schema.items() if k != "$schema"}

    # Handle anyOf before applying the rest of the constraints so that one of
    # the subschemas can succeed.
    if "anyOf" in schema:
        options = schema["anyOf"]
        if not isinstance(options, Sequence):
            raise SchemaValidationError(path, "'anyOf' must be an array")
        for option in options:
            try:
                _validate(instance, option, path)
            except SchemaValidationError:
                continue
            else:
                break
        else:
            raise SchemaValidationError(path, "Value does not satisfy any allowed schema")

    if "type" in schema:
        _ensure_type(instance, schema["type"], path)

    if "enum" in schema:
        allowed = schema["enum"]
        if instance not in allowed:
            raise SchemaValidationError(path, f"Expected one of {allowed!r}, got {instance!r}")

    if "format" in schema and isinstance(instance, str):
        _validate_format(instance, schema["format"], path)

    if "minimum" in schema and isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema["minimum"]
        if instance < minimum:
            raise SchemaValidationError(path, f"Value {instance!r} is less than minimum {minimum!r}")

    if isinstance(instance, Mapping):
        _validate_object(instance, schema, path)
    elif isinstance(instance, (list, tuple)):
        _validate_array(instance, schema, path)


def _validate_object(instance: Mapping[str, Any], schema: Mapping[str, Any], path: Sequence[Any]) -> None:
    if schema.get("type") not in (None, "object", ["object"]):
        return

    required = schema.get("required", [])
    for key in required:
        if key not in instance:
            raise SchemaValidationError(path + (key,), "Missing required property")

    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties", True)

    if "minProperties" in schema and len(instance) < schema["minProperties"]:
        raise SchemaValidationError(path, "Object has fewer properties than allowed")

    for key, value in instance.items():
        if key in properties:
            _validate(value, properties[key], path + (key,))
        else:
            if additional is False:
                raise SchemaValidationError(path + (key,), "Additional properties are not allowed")
            if isinstance(additional, Mapping):
                _validate(value, additional, path + (key,))


def _validate_array(instance: Sequence[Any], schema: Mapping[str, Any], path: Sequence[Any]) -> None:
    if schema.get("type") not in (None, "array", ["array"]):
        return

    if "minItems" in schema and len(instance) < schema["minItems"]:
        raise SchemaValidationError(path, "Array has fewer items than allowed")

    item_schema = schema.get("items")
    if isinstance(item_schema, Mapping):
        for index, item in enumerate(instance):
            _validate(item, item_schema, path + (index,))


def _ensure_type(instance: Any, expected: Any, path: Sequence[Any]) -> None:
    expected_types = expected if isinstance(expected, (list, tuple)) else [expected]
    if not any(_matches_type(instance, schema_type) for schema_type in expected_types):
        readable = ", ".join(str(t) for t in expected_types)
        raise SchemaValidationError(path, f"Expected type {readable}, got {_describe_type(instance)}")


def _matches_type(instance: Any, schema_type: Any) -> bool:
    if schema_type == "object":
        return isinstance(instance, Mapping)
    if schema_type == "array":
        return isinstance(instance, (list, tuple))
    if schema_type == "string":
        return isinstance(instance, str)
    if schema_type == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if schema_type == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if schema_type == "boolean":
        return isinstance(instance, bool)
    if schema_type == "null":
        return instance is None
    return False


def _describe_type(instance: Any) -> str:
    if isinstance(instance, Mapping):
        return "object"
    if isinstance(instance, (list, tuple)):
        return "array"
    if isinstance(instance, str):
        return "string"
    if isinstance(instance, bool):
        return "boolean"
    if isinstance(instance, (int, float)):
        return "number"
    if instance is None:
        return "null"
    return type(instance).__name__


def _validate_format(value: str, format_type: str, path: Sequence[Any]) -> None:
    if format_type == "date-time":
        try:
            # Support the "Z" suffix used in the sample configs.
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:  # pragma: no cover - defensive
            raise SchemaValidationError(path, f"Invalid date-time format: {value!r}") from exc
    elif format_type == "uri":
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise SchemaValidationError(path, f"Invalid URI: {value!r}")
    # Unknown formats are ignored – the schema does not rely on them.
