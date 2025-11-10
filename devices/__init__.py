import os
import importlib

def get_metadata():
    """Dynamically discover and collect metadata from all device modules."""
    metadata = {}
    current_dir = os.path.dirname(__file__)
    for filename in os.listdir(current_dir):
        if filename.endswith(".py") and filename != "__init__.py":
            module_name = filename[:-3]  # Remove .py extension
            module_path = f"{__name__}.{module_name}"  # Full import path
            try:
                module = importlib.import_module(module_path)
                if hasattr(module, "get_metadata"):
                    metadata[module_name] = module.get_metadata()
            except Exception as e:
                print(f"Failed to load module {module_name}: {e}")
    return metadata
