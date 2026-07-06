"""Plugin SDK: extension points for third-party code, without touching
core platform files.

A plugin is a single .py file, placed in the configured plugins directory
(`config.PluginSettings.directory`, default `./plugins_installed`),
defining a module-level `PLUGIN` object of type `plugins.base.Plugin`.
Every file in that directory is imported once at startup and its
`PLUGIN.tools`, `PLUGIN.validation_rules`, and `PLUGIN.backends` are
merged into the platform's shared registries (`plugins.loader.apply`,
called from `apps.context.build_context`) — the same registries the
built-in tools/rules/backends live in, so a plugin tool is dispatched by
`/tools/{name}` and MCP `call_tool` exactly like a built-in one.

See `examples/plugins/example_plugin.py` for a complete, runnable example.

Scope note: a plugin can register a CAD backend (via `cad.registry`) that
its own tools call directly, but that backend cannot currently be selected
as the *default* session backend through the `cad.backend` config field —
config validation happens before plugins are loaded, so a not-yet-known
backend name would fail validation. See docs/architecture.md, Phase 9.
"""
