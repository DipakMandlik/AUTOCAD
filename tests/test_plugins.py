"""Plugin SDK tests.

`apply()` is tested against throwaway registries (plain lists/dicts), never
the real global `TOOL_REGISTRY`/`TOOLS_BY_NAME`/`DEFAULT_RULES` — mutating
those in a test would leak into every other test in the session (e.g.
test_tools.py's exact-tool-set assertion). The build_context() wiring test
verifies the *contract* (it calls discover_and_apply with the configured
directory) via monkeypatching the loader function itself, for the same
reason: zero risk of touching real shared state.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from plugins.base import Plugin
from plugins.loader import apply, discover, discover_and_apply

EXAMPLE_PLUGIN = Path(__file__).resolve().parent.parent / "examples" / "plugins" / "example_plugin.py"


def _fake_rule(plan, geometry):
    return []


@pytest.fixture
def registries():
    return {"tool_registry": [], "tools_by_name": {}, "validation_rules": [], "backends": {}}


def register_backend_into(registries):
    def _register(name, factory):
        registries["backends"][name] = factory

    return _register


# --- discover() ----------------------------------------------------------


def test_discover_returns_empty_list_for_missing_directory():
    assert discover("/does/not/exist/at/all") == []


def test_discover_finds_the_real_example_plugin(tmp_path):
    shutil.copy(EXAMPLE_PLUGIN, tmp_path / "example_plugin.py")
    found = discover(str(tmp_path))
    assert len(found) == 1
    assert found[0].name == "example-plugin"
    assert len(found[0].tools) == 1
    assert found[0].tools[0].name == "draw_regular_polygon"
    assert len(found[0].validation_rules) == 1


def test_discover_skips_files_starting_with_underscore(tmp_path):
    (tmp_path / "_ignored.py").write_text("PLUGIN = None\n")
    assert discover(str(tmp_path)) == []


def test_discover_skips_file_with_no_plugin_object(tmp_path):
    (tmp_path / "not_a_plugin.py").write_text("x = 1\n")
    assert discover(str(tmp_path)) == []


def test_discover_skips_file_that_raises_on_import(tmp_path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n")
    (tmp_path / "good.py").write_text(
        "from plugins.base import Plugin\nPLUGIN = Plugin(name='good')\n"
    )
    found = discover(str(tmp_path))
    assert [p.name for p in found] == ["good"]


def test_discover_skips_module_level_value_that_is_not_a_plugin(tmp_path):
    (tmp_path / "wrong_type.py").write_text("PLUGIN = {'name': 'not-a-plugin-instance'}\n")
    assert discover(str(tmp_path)) == []


# --- apply() ---------------------------------------------------------------


def test_apply_adds_tools_to_both_registry_and_lookup(registries):
    from apps.server.tools import ToolSpec

    tool = ToolSpec("frobnicate", "does a thing", {"type": "object", "properties": {}}, lambda a, c: {})
    plugin = Plugin(name="p1", tools=[tool])

    applied = apply(
        [plugin],
        registries["tool_registry"],
        registries["tools_by_name"],
        registries["validation_rules"],
        register_backend_into(registries),
    )

    assert registries["tool_registry"] == [tool]
    assert registries["tools_by_name"]["frobnicate"] is tool
    assert applied == ["p1: tool 'frobnicate'"]


def test_apply_skips_tool_name_collision_without_raising(registries):
    from apps.server.tools import ToolSpec

    empty_schema = {"type": "object", "properties": {}}
    existing = ToolSpec("draw_line", "built-in", empty_schema, lambda a, c: {})
    registries["tools_by_name"]["draw_line"] = existing
    conflicting = ToolSpec("draw_line", "plugin version", empty_schema, lambda a, c: {})
    plugin = Plugin(name="p1", tools=[conflicting])

    applied = apply(
        [plugin],
        registries["tool_registry"],
        registries["tools_by_name"],
        registries["validation_rules"],
        register_backend_into(registries),
    )

    assert registries["tools_by_name"]["draw_line"] is existing  # not overwritten
    assert applied == []


def test_apply_adds_validation_rules(registries):
    plugin = Plugin(name="p1", validation_rules=[_fake_rule])
    apply(
        [plugin],
        registries["tool_registry"],
        registries["tools_by_name"],
        registries["validation_rules"],
        register_backend_into(registries),
    )
    assert registries["validation_rules"] == [_fake_rule]


def test_apply_does_not_add_the_same_rule_twice(registries):
    registries["validation_rules"].append(_fake_rule)
    plugin = Plugin(name="p1", validation_rules=[_fake_rule])
    apply(
        [plugin],
        registries["tool_registry"],
        registries["tools_by_name"],
        registries["validation_rules"],
        register_backend_into(registries),
    )
    assert registries["validation_rules"] == [_fake_rule]  # still just one


def test_apply_registers_backends_via_provided_function(registries):
    factory = object()
    plugin = Plugin(name="p1", backends={"my_backend": factory})
    apply(
        [plugin],
        registries["tool_registry"],
        registries["tools_by_name"],
        registries["validation_rules"],
        register_backend_into(registries),
    )
    assert registries["backends"]["my_backend"] is factory


def test_discover_and_apply_end_to_end_with_real_example_plugin(tmp_path, registries):
    shutil.copy(EXAMPLE_PLUGIN, tmp_path / "example_plugin.py")
    applied = discover_and_apply(
        str(tmp_path),
        registries["tool_registry"],
        registries["tools_by_name"],
        registries["validation_rules"],
        register_backend_into(registries),
    )
    assert "example-plugin: tool 'draw_regular_polygon'" in applied
    assert "draw_regular_polygon" in registries["tools_by_name"]


def test_example_plugin_tool_actually_draws(tmp_path, registries):
    from apps.context import ServerContext
    from cad.registry import get_backend
    from engine.planner.planner import Planner
    from engine.validator.engine import ValidationEngine
    from nlp.fallback import FallbackParser
    from storage.store import ProjectStore

    shutil.copy(EXAMPLE_PLUGIN, tmp_path / "example_plugin.py")
    discover_and_apply(
        str(tmp_path),
        registries["tool_registry"],
        registries["tools_by_name"],
        registries["validation_rules"],
        register_backend_into(registries),
    )

    ctx = ServerContext(
        planner=Planner(),
        validator=ValidationEngine(rules=list(registries["validation_rules"])),
        backend=get_backend("dxf", output_dir=str(tmp_path / "output")),
        color_parser=FallbackParser(),
        project_store=ProjectStore(str(tmp_path / "projects")),
    )

    result = registries["tools_by_name"]["draw_regular_polygon"].handler(
        {"center": [0, 0], "radius": 10, "sides": 6}, ctx
    )
    assert result["success"] is True
    assert result["entity"]["type"] == "polyline"
    assert len(result["entity"]["points"]) == 6

    # the plugin's own validation rule should fire (default layer "0")
    assert any(w["code"] == "default_layer_used" for w in result["warnings"])


def test_example_plugin_rejects_too_few_sides(tmp_path, registries):
    from apps.context import ServerContext
    from cad.registry import get_backend
    from engine.planner.planner import Planner
    from engine.validator.engine import ValidationEngine
    from nlp.fallback import FallbackParser
    from storage.store import ProjectStore

    shutil.copy(EXAMPLE_PLUGIN, tmp_path / "example_plugin.py")
    discover_and_apply(
        str(tmp_path),
        registries["tool_registry"],
        registries["tools_by_name"],
        registries["validation_rules"],
        register_backend_into(registries),
    )
    ctx = ServerContext(
        planner=Planner(),
        validator=ValidationEngine(),
        backend=get_backend("dxf", output_dir=str(tmp_path / "output")),
        color_parser=FallbackParser(),
        project_store=ProjectStore(str(tmp_path / "projects")),
    )
    result = registries["tools_by_name"]["draw_regular_polygon"].handler(
        {"center": [0, 0], "radius": 10, "sides": 2}, ctx
    )
    assert result["success"] is False


# --- build_context() wiring (contract only, no real global mutation) -------


def test_build_context_passes_configured_plugin_directory_to_loader(monkeypatch, tmp_path):
    from apps.context import build_context
    from config import Settings

    calls = []

    def fake_discover_and_apply(directory, tool_registry, tools_by_name, validation_rules, register_fn):
        calls.append(directory)
        return []

    monkeypatch.setattr("plugins.loader.discover_and_apply", fake_discover_and_apply)

    settings = Settings(
        output={"directory": str(tmp_path / "output")},
        storage={"directory": str(tmp_path / "projects")},
        plugins={"directory": "/configured/plugin/dir"},
    )
    build_context(settings)

    assert calls == ["/configured/plugin/dir"]
