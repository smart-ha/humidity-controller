"""Load the HA-free modules of the integration without Home Assistant.

``custom_components/humidity_controller/__init__.py`` imports Home Assistant,
so the package is registered here as a bare namespace (its ``__init__`` is
never executed) and the pure modules — ``const``, ``control``, ``schedule``,
``entry`` — are imported from it as usual.
"""
import sys
import types
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "humidity_controller"

if "humidity_controller" not in sys.modules:
    package = types.ModuleType("humidity_controller")
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules["humidity_controller"] = package
