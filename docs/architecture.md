# Architecture — Phase 2 Redesign (Core Framework MVP)

This document explains the architecture being built in Phase 3, and why,
following the findings in `docs/architecture-review.md`. Scope for this
pass is the **Core Framework MVP**: a real planning → geometry → validation
→ execution pipeline with a fully working, testable headless backend, and a
correctly-refactored AutoCAD COM backend that cannot be exercised in this
Linux sandbox (verification on Windows is the user's responsibility). It
deliberately does not yet include the dashboard, REST API, plugin
marketplace, multi-format import (PDF/image/sketch), or the standards
knowledge base — those are later phases layered on top of this spine.

## Design goals driving every decision below

1. **Testable without Windows or a CAD license.** The single biggest reason
   the original repo has zero tests is that its only backend requires a
   live, GUI-attached AutoCAD COM object. Everything that can be backend-
   agnostic (planning, geometry, validation) must not import `win32com`,
   and there must be at least one backend (`dxf`) that runs anywhere.
2. **One source of truth per concern.** The review's top complaint was
   ~600 duplicated lines across `process_command` and `handle_call_tool`,
   plus config loaded independently in three files. Every tool is defined
   once (name, JSON schema, handler) and both the direct-tool-call path and
   the natural-language path route through the same planner → validator →
   backend pipeline.
3. **Plan before you draw.** The old code executed one COM call per tool
   invocation with no intermediate artifact. The new pipeline always
   produces a serializable `DrawingPlan` (typed, Pydantic-validated) before
   anything touches a backend, which is what makes validation, dry-run, and
   future undo/redo possible at all.
4. **Backend is an interchangeable adapter, not a fork in every function.**
   `CADBackend` is an abstract interface; `DXFBackend` and `AutoCADBackend`
   are two implementations of it. Adding ZWCAD/GstarCAD-specific quirks or
   a future FreeCAD backend means writing a new adapter class, not editing
   `cad_controller.py`'s `if/elif` chain in two places.

## Module map

```
engine/
  geometry/
    primitives.py   — Pydantic entity models (Line, Circle, Arc, Ellipse,
                       Polyline, Rectangle, Text, Hatch, Dimension) + DrawingPlan.
                       This is the shared vocabulary every other module speaks.
    engine.py        — GeometryEngine: derived geometry (rectangle -> corner
                       points, bounding boxes) used by validation and backends.
  planner/
    intent.py        — IntentDetector protocol + the concrete detector backed
                       by nlp.fallback today; swappable for an LLM-backed
                       detector later without touching the planner.
    planner.py        — Planner: text -> Intent -> typed DrawingPlan, and a
                       direct path (already-typed tool args -> DrawingPlan)
                       used by explicit MCP tool calls.
  validator/
    rules.py          — individual rule functions (invalid geometry, invalid
                       lineweight, duplicate entities, missing text, layer
                       naming) each returning a list of Issues.
    engine.py         — ValidationEngine: runs all rules, produces a
                       ValidationReport, and autofix() for safe corrections.
cad/
  backend.py           — CADBackend ABC (start/is_running/execute/save/
                        create_layer) + ExecutionResult, shared by every backend.
  registry.py           — name -> backend class registry/factory driven by config.
dxf/
  backend.py             — DXFBackend(CADBackend): real, headless, ezdxf-based.
                          The only backend exercised by the test suite.
autocad/
  backend.py              — AutoCADBackend(CADBackend): refactored COM driver
                          for AutoCAD/GstarCAD/ZWCAD. Fixes the `old_app`
                          NameError, the duplicated app-id lookup, per-entity
                          Regen, unbounded layer scans, and unsanitized save
                          paths identified in the review. Windows-only;
                          import guarded so the rest of the platform still
                          runs on Linux/macOS/CI.
nlp/
  fallback.py              — Refactored regex/keyword parser (the old
                          nlp_processor.py), demoted to what it actually is:
                          a cheap, offline fallback intent source, not "the
                          NLP layer." Bugs fixed, single keyword table.
apps/
  context.py                 — ServerContext (planner + validator + backend)
                          and build_context(settings). The one place that
                          wires config into a running context; both apps
                          below import it instead of each building their own.
  server/
    tools.py                  — ToolSpec registry: one definition per tool
                          (name, Pydantic args model, handler), plus
                          run_pipeline() (validate -> autofix -> execute).
                          Single source for MCP `list_tools` schemas *and*
                          `call_tool` dispatch *and* `process_command` *and*
                          the REST API below.
    server.py                  — MCP stdio server wiring: config -> backend
                          registry -> planner/validator -> tool registry.
  api/
    main.py                    — REST API (FastAPI): a second transport over
                          the exact same ServerContext/TOOL_REGISTRY/
                          run_pipeline, adding two capabilities MCP's
                          one-tool-per-call model doesn't: a validate-only
                          dry run, and executing a multi-entity DrawingPlan
                          in a single call. See "Phase 4" below.
config.py                    — One Pydantic settings model, env-overridable
                          (`CADMCP_*`), replacing the three independent
                          `config.json` readers in the original code.
tests/                        — pytest suite; everything except a possible
                          future `test_autocad_backend.py` runs on Linux.
```

## Pipeline (matches the master vision, scoped to what exists today)

```
MCP tool call  ──┐                      Natural language ("process_command")
(typed args)     │                                │
                 ▼                                ▼
        Planner.plan_from_operation      Planner.plan_from_text
                 │                                │  (IntentDetector: nlp.fallback)
                 └───────────────┬────────────────┘
                                  ▼
                            DrawingPlan (Pydantic, serializable)
                                  ▼
                     GeometryEngine (derived geometry, bounding boxes)
                                  ▼
                    ValidationEngine.validate() → autofix() if safe
                                  ▼
                         CADBackend.execute(plan)
                        (DXFBackend or AutoCADBackend,
                         selected by config.cad.backend)
                                  ▼
                          ExecutionResult (per-entity handles/errors)
```

Every tool call — whether it came from a typed MCP tool invocation or a
free-text `process_command` string — flows through the *same* plan/validate/
execute pipeline. That is what eliminates the duplicated dispatch logic and
is the prerequisite for adding an LLM-backed planner later without another
rewrite.

## Phase 4: REST API

Once the core framework was in place and tested, the next highest-leverage
addition was a second transport: a REST API (`apps/api/main.py`), because
(a) it is the prerequisite for a future dashboard, and (b) building it
immediately proved out the Phase 3 claim that the engine is
transport-agnostic — `apps/api/main.py` contains zero planning, validation,
or execution logic. It only wires `ServerContext` and `TOOL_REGISTRY` (moved
to `apps/context.py` so both apps share one wiring path) into HTTP routes:

- `GET /health`, `GET /tools` — introspection
- `POST /tools/{name}` — the same single-operation dispatch as an MCP tool call
- `POST /drawings/validate` — validate a full `DrawingPlan` without executing
- `POST /drawings/execute` — execute a multi-entity `DrawingPlan` in one call,
  something the one-tool-per-MCP-call model can't do directly

Writing the REST test suite (`tests/test_api.py`) surfaced a real bug in
`run_pipeline`: autofix was gated on `report.is_valid`, which only reflects
*errors*. `duplicate_entity` is warning-severity, so a plan whose only
problem was a duplicate entity skipped autofix entirely and both copies got
drawn. Fixed by gating on "any autofixable issue is present" instead —
caught before it shipped, by the second transport's tests exercising a
multi-entity path the first transport's tests hadn't.

## What is still deferred (not stubbed)

The following from the master vision are **not** built yet, and no
placeholder directories were created for them (an empty `dashboard/` or
`plugins/` folder communicates nothing and just adds noise):

- Dashboard / plugin SDK
- Multi-format import (PDF, image, hand sketch, Excel/CSV, flowcharts)
- Symbol libraries / ANSI-ISO-IEC standards knowledge base
- DWG/SVG/PDF/LISP/SCR export (DXF is the one working export format for now)
- FreeCAD/Fusion/SolidWorks/Revit backends (the `CADBackend` interface is
  designed so these can be added later as new adapters)
- Persistence/project/revision-history layer (both apps currently hold one
  in-memory backend document for the process lifetime)

These become straightforward additions once the spine under them is proven
— building them before that spine exists would mean rewriting them anyway.
