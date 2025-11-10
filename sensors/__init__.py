import os
import importlib
from loguru import logger


def get_metadata():
    """Dynamically discover and collect metadata from all sensor modules."""
    metadata = {}
    current_dir = os.path.dirname(__file__)

    # Configure logging for module discovery
    logger.info("Discovering sensor metadata...", extra={"log_type": "system", "context": "sensors"})

    for filename in os.listdir(current_dir):
        if filename.endswith(".py") and filename != "__init__.py":
            module_name = filename[:-3]  # Remove .py extension
            module_path = f"{__name__}.{module_name}"  # Full import path
            try:
                module = importlib.import_module(module_path)
                if hasattr(module, "get_metadata"):
                    module_metadata = module.get_metadata()
                    metadata[module_name] = module_metadata
                    logger.info(f"Loaded metadata from module: {module_name}",
                                extra={"log_type": "system", "context": "sensors"})
                else:
                    logger.warning(f"Module {module_name} does not define 'get_metadata'",
                                   extra={"log_type": "system", "context": "sensors"})
            except Exception as e:
                logger.error(f"Failed to load module {module_name}: {e}",
                             extra={"log_type": "system", "context": "sensors"})

    logger.info("Sensor metadata discovery completed.", extra={"log_type": "system", "context": "sensors"})
    return metadata
