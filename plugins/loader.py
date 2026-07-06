"""Discover plugin files and merge their contributions into the shared
tool registry, validator rule list, and CAD backend registry.

`apply()` takes its registries as explicit arguments rather than importing
the real global ones — that's what makes it unit-testable against
throwaway lists/dicts with zero risk of polluting state shared with other
tests. The one production call site is `apps.context.build_context`,
which passes the real `TOOL_REGISTRY`/`TOOLS_BY_NAME`/`DEFAULT_RULES`.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Callable, List

from plugins.base import Plugin

logger = logging.getLogger("plugins")


def discover(directory: str) -> List[Plugin]:
    """Import every .py file in `directory` and collect its PLUGIN object.

    Returns an empty list if the directory doesn't exist — plugins are
    opt-in, not a required part of startup, so a missing directory is not
    an error condition.
    """
    path = Path(directory)
    if not path.is_dir():
        return []

    found: List[Plugin] = []
    for file in sorted(path.glob("*.py")):
        if file.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"_cadmcp_plugin_{file.stem}", file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            logger.exception("failed to load plugin file %s; skipped", file)
            continue
        plugin = getattr(module, "PLUGIN", None)
        if not isinstance(plugin, Plugin):
            logger.warning("%s has no module-level PLUGIN object; skipped", file)
            continue
        found.append(plugin)
    return found


def apply(
    plugins: List[Plugin],
    tool_registry: list,
    tools_by_name: dict,
    validation_rules: list,
    register_backend_fn: Callable[[str, Callable], None],
) -> List[str]:
    """Merge plugin contributions into the given registries in place.
    Returns a human-readable list of what was applied, for logging."""
    applied: List[str] = []
    for plugin in plugins:
        for tool in plugin.tools:
            if tool.name in tools_by_name:
                logger.warning(
                    "plugin '%s': tool '%s' conflicts with an existing tool; skipped",
                    plugin.name,
                    tool.name,
                )
                continue
            tool_registry.append(tool)
            tools_by_name[tool.name] = tool
            applied.append(f"{plugin.name}: tool '{tool.name}'")

        for rule in plugin.validation_rules:
            if rule in validation_rules:
                continue
            validation_rules.append(rule)
            applied.append(f"{plugin.name}: validation rule '{rule.__name__}'")

        for backend_name, factory in plugin.backends.items():
            register_backend_fn(backend_name, factory)
            applied.append(f"{plugin.name}: backend '{backend_name}'")

    return applied


def discover_and_apply(
    directory: str,
    tool_registry: list,
    tools_by_name: dict,
    validation_rules: list,
    register_backend_fn: Callable[[str, Callable], None],
) -> List[str]:
    plugins = discover(directory)
    if plugins:
        logger.info("discovered %d plugin(s) in %s", len(plugins), directory)
    return apply(plugins, tool_registry, tools_by_name, validation_rules, register_backend_fn)
