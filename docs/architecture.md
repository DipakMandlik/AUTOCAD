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

## Phase 8: AutoCAD Script and AutoLISP export

The last output format from the master vision's list that's actually
verifiable without a proprietary binary writer: `export/script.py` adds
`.scr` and `.lsp` generation. Both share one model — for each entity, a
"command block" (the sequence of command-line inputs exactly as if typed
interactively into AutoCAD) — rendered two ways: `render_scr` joins the
tokens with newlines, `render_lisp` wraps each block in one `(command
...)` form. This is the one export path that needs neither `autocad`
backend's `pywin32`/Windows/COM stack nor `dxf` backend's ezdxf: run the
`.scr` via AutoCAD's `SCRIPT` command, or load the `.lsp` via `APPLOAD`,
on any machine with AutoCAD installed, no COM automation required.

**Hatch is deliberately unsupported here**, unlike everywhere else in the
platform. The COM backend's hatch support (`AddHatch`) is a direct
object-model call and reliable; scripting `-HATCH` means replaying a
multi-step interactive prompt sequence that has changed across AutoCAD
versions and depends on current dialog/settings state. There was no way to
verify a guessed sequence in this environment, and a plausible-looking
script that silently does the wrong thing is worse than clearly not
supporting it. `unsupported_entities()` reports which plan entries were
skipped so every layer (tool response, REST response, dashboard log) can
surface that to the user rather than silently dropping content.

New tools (`export_script`, `export_lisp`) and REST endpoints
(`GET /drawings/current/export`, `GET /projects/{id}/export`,
`?format=scr|lisp`, real `text/plain` downloads with a
`Content-Disposition` header) follow the same pattern as Phase 7's
render endpoints — including a `_get_project_or_404` helper factored out
because this was the third endpoint pair needing the same project-lookup
try/except. The dashboard's download buttons call the tool endpoint first
(to log any hatch-skipped warning) before navigating to the download URL,
since a file download needs to see response headers a `fetch()` result
can log but a plain navigation is what actually makes the browser save it.

**Unverified**, same caveat as `autocad.backend`: there is no AutoCAD in
this environment to run the generated scripts against. The command
sequences follow documented AutoCAD command-line prompt order; verify
before relying on them.

## Phase 9: Plugin SDK

The master vision's last major architectural piece: "third-party
developers should be able to build plugins without modifying the core
platform." The existing architecture already had the right shape for
this — `TOOL_REGISTRY`/`TOOLS_BY_NAME`, `DEFAULT_RULES`, and
`cad.registry` are all just a list/dict a plugin can be merged into — so
this phase is mostly formalizing discovery and a stable data shape, not a
new subsystem.

- **`plugins/base.py`**: `Plugin` — a name/version plus three optional
  lists: `tools` (`ToolSpec`s), `validation_rules` (`RuleFn`s), and
  `backends` (name → `CADBackend` factory).
- **`plugins/loader.py`**: `discover(directory)` imports every `.py` file
  in the configured plugins directory (`config.PluginSettings.directory`,
  default `./plugins_installed`; a missing directory is a no-op, not an
  error) and collects each file's module-level `PLUGIN` object.
  `apply(plugins, tool_registry, tools_by_name, validation_rules,
  register_backend_fn)` merges those into the given registries — **in
  place, taking them as explicit arguments** rather than importing the
  real global ones directly. That is what makes `apply()` unit-testable
  against throwaway lists/dicts with zero risk of leaking a test plugin's
  tool into `test_tools.py`'s exact-tool-set assertion; the one production
  call site (`apps.context.build_context`) passes the real
  `TOOL_REGISTRY`/`TOOLS_BY_NAME`/`DEFAULT_RULES`.
- **`examples/plugins/example_plugin.py`**: a complete, runnable example —
  a `draw_regular_polygon` tool built entirely out of the existing
  `draw_polyline` operation (no new `Entity` type needed) plus a
  `default_layer_used` validation rule enforcing an organization
  convention that has no place in the built-in rule set because it's a
  policy choice, not a geometric correctness check. Both extension points
  were verified end-to-end: the tool actually draws a hexagon, and the
  rule actually fires as a warning in the response.

**A real circular-import constraint shaped the wiring.** `apps.server.tools`
imports `ServerContext` from `apps.context` (to type-hint tool handlers),
so `apps.context` cannot import `apps.server.tools` at module load time —
that would be a cycle. `build_context()` needs `TOOL_REGISTRY`/
`TOOLS_BY_NAME` to hand to the plugin loader, so `_load_plugins()` does
that import *inside the function body* instead of at module level: by the
time `build_context()` is actually called, both modules have finished
initializing, so the deferred import is safe. Plugin loading also has to
happen **before** `ValidationEngine()` is constructed in the same
function — `ValidationEngine.__init__` snapshots `DEFAULT_RULES` into
`self.rules` at construction time, so a plugin rule appended afterward
would silently never run.

**Scope boundary, stated plainly**: a plugin-registered backend can be
used directly by that plugin's own tool handlers (via
`cad.registry.get_backend("name", ...)`), but it cannot currently be
selected as the *default* session backend through the `cad.backend`
config field. Config validation (`CADSettings._known_backend`, which
calls `cad.registry.available_backends()`) happens when `Settings.load()`
runs, before plugins are loaded — a not-yet-registered backend name would
fail that validation. Solving this would mean loading plugins before
parsing config, which has its own ordering problems (where would the
plugins-directory setting come from?); not attempted here.

## Phase 10: Symbol library

The master vision's "reusable engineering libraries" section, scoped down
from an exhaustive per-discipline catalog (mechanical, electrical, P&ID,
hydraulic, pneumatic, civil, architectural, HVAC, structural, pipeline,
instrumentation, networking, industrial automation, warehouse,
manufacturing — fifteen disciplines) to nine real, recognizable, tested
symbols across three: electrical (resistor, capacitor, ground,
battery cell), piping/P&ID (gate valve, pump), and architectural (door
swing, window, north arrow). The point of this phase was proving the
*pattern* generalizes, not exhausting the catalog — adding a tenth symbol
is one function and one catalog entry, not a new subsystem.

- **`symbols/library.py`**: each symbol is authored once, in a canonical
  local coordinate space, as a plain function `(origin, scale, rotation)
  -> List[Entity]`. `_transform()` handles scale-then-rotate-then-translate
  uniformly for points; `ArcEntity`'s start/end angles need the rotation
  added directly, since the entity model stores absolute angles rather
  than an angle relative to a local frame. `SymbolDefinition` pairs a
  builder with catalog metadata (discipline, description) in
  `SYMBOL_LIBRARY`, a plain dict — no registry abstraction needed, since
  unlike `CADBackend` there's no reason to swap the whole symbol set at
  runtime.
- **`insert_symbol`/`list_symbols` tools + `GET /symbols`,
  `GET /symbols/{name}/preview`**. `insert_symbol` is the first built-in
  tool whose response is genuinely multi-entity (a capacitor is 4
  separate `LineEntity`s), so its handler uses `run_pipeline`/
  `result_entries` directly — the same pattern `load_project` already
  established — rather than the single-entity `execute_plan` every
  `draw_*` tool uses. `/symbols/{name}/preview` reuses `export.renderer`
  as-is: no symbol-specific rendering code, just a `DrawingPlan` built
  from one symbol at the origin.
- **Dashboard "Symbols" panel**: a 3-column grid of catalog cards, each
  with a live `<img src="/symbols/{name}/preview?format=svg">` thumbnail
  and an "Insert" button using a shared position/scale/rotation input.
  Verified with Playwright that all 9 thumbnails actually load (not
  broken `<img>` tags) and that inserting a symbol draws the right entity
  count into the live preview.

**Scope boundary, stated plainly**: these are illustrative, recognizable
symbols, not literally ANSI/ISO/IEC-compliant ones — exact stroke widths,
proportions, and the approved symbol sets per discipline are licensed
standards documents (ASME Y14.5, IEC 60617, etc.) this project has no
access to. A real standards knowledge base (dimension rules, title block
standards, layer naming standards tied to a named standard like ISO 13567)
is a content-curation effort orders of magnitude larger than a geometry
generator and was not attempted here — see the deferred list below.

## What is still deferred (not stubbed)

The following from the master vision are **not** built yet, and no
placeholder directories were created for them (an empty `dashboard`
section folder communicates nothing and just adds noise):

- Dashboard sections that need richer UI/backend support: Templates,
  Execution Queue, Logs, Performance, Settings
- Multi-format import (PDF, image, hand sketch, Excel/CSV, flowcharts)
- The ANSI/ISO/IEC/ASME/DIN/JIS standards knowledge base itself (dimension
  rules, title block standards, named-standard layer conventions) — the
  symbol *library* now exists (Phase 10), but it is illustrative geometry,
  not licensed standards content
- Additional symbol disciplines beyond electrical/piping/architectural
  (mechanical, hydraulic, pneumatic, civil, HVAC, structural,
  instrumentation, networking, industrial automation, warehouse,
  manufacturing) — same pattern as Phase 10, just more of them
- DWG export and hatch support in `.scr`/`.lsp` (DXF, SVG, PNG, SCR, and
  LISP all work now for non-hatch geometry)
- FreeCAD/Fusion/SolidWorks/Revit backends (the `CADBackend` interface is
  designed so these can be added later as new adapters)
- True multi-document/multi-tenant project isolation (see the Phase 6 note above)
- Plugin-provided CAD backends as the selectable default session backend
  (see the Phase 9 scope boundary above)

These become straightforward additions once the spine under them is proven
— building them before that spine exists would mean rewriting them anyway.
