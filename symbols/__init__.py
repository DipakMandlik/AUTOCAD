"""Parametric symbol library: reusable engineering symbols as generator
functions producing already-validated `Entity` lists.

Each symbol is defined once, in a canonical unit-size local coordinate
space, and instantiated anywhere via `SymbolDefinition.build(origin, scale,
rotation)`. That's the whole trick — the library is a set of small
geometry generators, not a new subsystem: `insert_symbol`
(`apps/server/tools.py`) turns a symbol into an ordinary multi-entity
`DrawingPlan` and runs it through the exact same `run_pipeline` as
anything else, so validation, autofix, and execution all work identically.

Scope boundary, stated plainly: these are illustrative, recognizable
symbols, not literally ANSI/ISO/IEC-compliant ones. The real standards
(exact stroke widths, proportions, approved symbol sets per discipline)
are licensed standards documents this project has no access to — see
docs/architecture.md, Phase 10.
"""
