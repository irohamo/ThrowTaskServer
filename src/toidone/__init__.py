"""Compatibility imports for clients configured before the ThrowTask rename."""

import importlib
import sys

from throwtask import __version__ as __version__

for _name in ("client", "config", "errors", "models", "storage", "recurrence"):
    _module = importlib.import_module(f"throwtask.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module
