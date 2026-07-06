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

## Phase 5: Dashboard

A dashboard needs the REST API to already exist, which is why it followed
rather than preceded Phase 4. Scope was deliberately narrowed from the
master vision's thirteen-section dashboard (Projects, Templates, Symbol
Libraries, Drawing Explorer, Validation, Revisions, History, AI Chat,
Execution Queue, Logs, Performance, Plugins, Settings) to the sections that
map to something the platform can actually do today: **AI Chat**, a
**tool explorer**, a **drawing preview**, and **validation** — a project/
revision/plugin/log system would be UI over persistence and a plugin SDK
that don't exist yet, and building that chrome now would just be inert.

`apps/dashboard/static/` is a dependency-free vanilla HTML/CSS/JS app —
no framework, no build step — served by the REST API itself via
`StaticFiles` mounted at `/dashboard` (`apps/api/main.py`), so there is no
CORS configuration to get wrong: the dashboard calls same-origin `/tools`,
`/drawings/validate`, and `/drawings/execute` directly. It maintains its
own client-side list of drawn entities purely to render an SVG preview;
that list is explicitly not the source of truth (the backend document is)
and the UI says so.

Two things needed a small backend change to make the dashboard possible:

- `execute_plan`/`execute_drawing` didn't return the entity that was
  actually drawn, only its handle — fine for an MCP client that already
  knows what it asked for, useless for a `process_command` (natural
  language) response, since the caller has no other way to learn what the
  parser resolved the request to. Both endpoints now echo the
  (possibly-autofixed) entity's own data back.
- Nothing else — `apps/dashboard` and `apps/api` are the only new code;
  `engine/`, `cad/`, `dxf/`, `nlp/` were untouched.

The dashboard was verified running against a live `uvicorn` dev server and
driven with Playwright/Chromium in this environment: chat-driven natural
language draw, a direct tool call with a JSON args form, a validate-only
dry run showing errors/warnings, and a save call all confirmed working,
with the SVG preview rendering coordinates correctly (y-up, matching the
engine's convention, despite SVG's native y-down coordinate system).
Automated browser tests are not part of the CI suite — that would need
`playwright install` as a CI step, which was judged out of scope for this
pass; the REST API's own pytest suite already covers every endpoint the
dashboard depends on, including that the static files are served correctly.

## Phase 6: Persistence and revision history

The biggest gap the Phase 5 deferred list called out was "a persistence/
project/revision-history layer" — without it, three whole dashboard
sections (Projects, Revisions, History) had nothing to be built against.
This phase closes that gap, scoped to what's actually needed rather than a
general-purpose document database:

- **`storage/`** — `Project`/`Revision` Pydantic models plus `ProjectStore`,
  a file-based store (one JSON document per project). No database
  dependency: at this scale a directory of JSON files is entirely
  adequate, and it follows the same interface-over-adapter shape as
  `cad.backend` — swapping in a real database later means replacing this
  module, not anything that calls it. Project ids double as filenames, so
  `ProjectStore` validates them against a safe-character pattern before
  touching the filesystem, the same path-traversal guard as
  `cad.backend.resolve_safe_path`.
- **`ServerContext.history`** — every entity actually drawn (i.e. present
  in a *successful* `EntityResult`) is appended here by `run_pipeline`.
  This is what "the current drawing" means for `get_current_drawing`,
  `create_project`, and `snapshot_project`; it is bookkeeping at the
  orchestration layer, not a backend capability, so it works identically
  regardless of which `CADBackend` is active and needed no backend changes.
- **New tools** (registered once, so both MCP and REST get them for free):
  `get_current_drawing`, `clear_current_drawing`, `create_project`,
  `list_projects`, `get_project`, `snapshot_project`, `load_project`.
  `load_project` re-runs a saved plan through `run_pipeline` exactly like
  any other multi-entity plan — it is not a separate code path.
- **REST route sugar** — `GET/POST /projects`, `GET /projects/{id}`,
  `POST /projects/{id}/revisions`, `POST /projects/{id}/load`,
  `GET /drawings/current`, `POST /drawings/clear`. Each is a thin wrapper
  calling the same tool handler `/tools/{name}` would; they exist purely
  for a more idiomatic REST surface, not because the generic dispatch
  couldn't already do this.
- **Dashboard "Projects" panel** — save-as-project, per-project snapshot
  and load buttons, and the drawing preview now syncs from
  `GET /drawings/current` after a load/clear instead of only tracking its
  own optimistic client-side state.

**What this is not**: full multi-tenant project isolation. There is still
exactly one live backend document per process. "Loading" a project redraws
its plan against whatever is currently open — if you already have entities
drawn, loading a project adds to them rather than opening it in isolation.
Multiple concurrent documents/projects would need the backend layer itself
to become multi-document-aware, which is a real architectural change, not
a small addition — deliberately not attempted here.

## Phase 7: Export engine (SVG/PNG rendering)

DXF was the only output format through Phase 6 — good for round-tripping
into real CAD software, useless for "show me the drawing" without opening
one. This phase adds real, CAD-accurate rendering:

- **`export/renderer.py`** — `render_svg(plan)` and `render_png(plan)`.
  Both build their own throwaway in-memory ezdxf document via `DXFBackend`
  and hand it to `ezdxf.addons.drawing` — a rendering concern layered on
  top of the DXF backend's entity-drawing logic, not a new execution path,
  and not tied to whatever `CADBackend` is actually configured for
  execution (rendering a plan works identically whether the live backend
  is `dxf` or `autocad`).
- SVG uses ezdxf's native SVG backend, whose only dependency is Pillow —
  now a required dependency, since `ezdxf.addons.drawing` imports `PIL.Image`
  unconditionally the moment it's imported at all. PNG additionally needs
  matplotlib, which is an optional extra (`pip install -e ".[render-png]"`);
  `render_png` raises a clear `RuntimeError` if it isn't installed, the
  same guarded-import pattern as `autocad.backend`'s `pywin32` dependency.
- **`render_current_drawing`** tool (SVG only — MCP tools return text
  content, and SVG is text) and REST `GET /drawings/current/render` /
  `GET /projects/{id}/render` (`?format=svg|png`, actual `image/svg+xml`
  or `image/png` responses, not JSON-wrapped).
- Dashboard: a toggle between the coarse hand-rolled preview and the real
  server-rendered SVG (`<img src="/drawings/current/render?format=svg">`,
  cache-busted on every refresh).

Two real bugs surfaced while building this, both from testing edge cases
rather than just the happy path:

- Rendering an **empty** plan crashed with `ValueError: empty bounding
  box` — ezdxf's `Page(0, 0)` auto-fit mode needs content to fit to, and
  even a fixed page size still needs *some* bounding box for content
  placement. Fixed by supplying an explicit trivial `render_box` when
  there's nothing to render, caught by a test for the empty-history case,
  not the well-populated one every manual check had used.
- The dashboard's render toggle initially left the coarse preview visible
  underneath the accurate one — `<svg id="canvas">` is an `SVGElement`,
  and setting `.hidden = true` on it in JavaScript does not reliably
  reflect to the content attribute the way it does on `HTMLElement`
  (confirmed by screenshot, not just code review). Fixed by managing
  visibility entirely through inline `style.display` instead of the
  `hidden` attribute/property on either element.

## What is still deferred (not stubbed)

The following from the master vision are **not** built yet, and no
placeholder directories were created for them (an empty `plugins/` folder
communicates nothing and just adds noise):

- Plugin SDK
- Dashboard sections that need a plugin SDK or richer UI first: Templates,
  Symbol Libraries, Execution Queue, Logs, Performance, Settings
- Multi-format import (PDF, image, hand sketch, Excel/CSV, flowcharts)
- Symbol libraries / ANSI-ISO-IEC standards knowledge base
- DWG/LISP/SCR export (DXF, SVG, and PNG now work; PDF would follow the
  same `export/renderer.py` pattern via a PDF-capable ezdxf drawing backend)
- FreeCAD/Fusion/SolidWorks/Revit backends (the `CADBackend` interface is
  designed so these can be added later as new adapters)
- True multi-document/multi-tenant project isolation (see the Phase 6 note above)

These become straightforward additions once the spine under them is proven
— building them before that spine exists would mean rewriting them anyway.
