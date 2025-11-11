"""Shared utilities for loading pluggable devices and sensors."""

from importlib import import_module
from typing import Callable, Dict, Iterable, Optional, Tuple

from loguru import logger


ConfigAdapter = Callable[[Dict], Dict]


def _resolve_module_and_class(model_name: str, package: str) -> Tuple[str, str]:
    """Resolve the import path and class name for a pluggable entity."""

    if "." in model_name:
        module_path, class_name = model_name.rsplit(".", 1)
        if not module_path.startswith(package):
            module_path = f"{package}.{module_path}"
    else:
        module_path = f"{package}.{model_name}"
        class_name = "".join(part.capitalize() for part in model_name.split("_"))

    return module_path, class_name


def _identity_config_adapter(config: Dict) -> Dict:
    """Return a shallow copy of the provided configuration."""

    return dict(config)


def load_entities(
    entity_definitions: Iterable[Dict],
    *,
    package: str,
    kind: str,
    config_adapter: Optional[ConfigAdapter] = None,
):
    """Load entities defined in configuration using dynamic imports."""

    adapter = config_adapter or _identity_config_adapter
    entities = []

    for entity_config in entity_definitions:
        try:
            model_name = entity_config["how"]
            module_path, class_name = _resolve_module_and_class(model_name, package)
            module = import_module(module_path)
            entity_class = getattr(module, class_name)

            entity = entity_class(adapter(entity_config))
            entities.append(entity)

            entity_id = entity_config.get("id", class_name)
            logger.info(f"Initialized {kind}: {entity_id}")
        except Exception as exc:  # pragma: no cover - defensive logging
            entity_name = entity_config.get("id", "unknown")
            logger.error(f"Failed to initialize {kind} {entity_name}: {exc}")

    return entities

