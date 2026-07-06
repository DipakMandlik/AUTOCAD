"""Backend registry: name -> CADBackend factory, with lazy module loading.

Lazy loading matters here specifically because `autocad.backend` imports
`win32com`/`pythoncom`, which do not exist outside Windows. Selecting the
"dxf" backend must never require those modules to be importable.
"""

from __future__ import annotations

import importlib
from typing import Callable, Dict, List

from cad.backend import CADBackend

_REGISTRY: Dict[str, Callable[..., CADBackend]] = {}

# Which module registers each backend name, imported on first use only.
_LAZY_MODULES = {
    "dxf": "dxf.backend",
    "autocad": "autocad.backend",
    "gcad": "autocad.backend",
    "gstarcad": "autocad.backend",
    "zwcad": "autocad.backend",
}


def register_backend(name: str, factory: Callable[..., CADBackend]) -> None:
    _REGISTRY[name.lower()] = factory


def get_backend(name: str, **kwargs) -> CADBackend:
    key = name.lower()
    if key not in _REGISTRY:
        module_name = _LAZY_MODULES.get(key)
        if module_name is None:
            raise ValueError(f"Unknown CAD backend '{name}'. Available: {sorted(_LAZY_MODULES)}")
        importlib.import_module(module_name)
    if key not in _REGISTRY:
        raise ValueError(f"Backend module for '{name}' did not register itself")
    return _REGISTRY[key](**kwargs)


def available_backends() -> List[str]:
    return sorted(_LAZY_MODULES)
