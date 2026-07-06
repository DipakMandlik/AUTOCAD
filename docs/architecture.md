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

## Phase 11: SVG import

The master vision's "multi-format import" spans PDF, hand sketches, Excel,
and more — most of that needs OCR/ML or heavyweight parsing libraries this
sandbox can't install or verify. SVG is the one format that's already a
CAD-adjacent, fully-specified vector format parseable with nothing beyond
the standard library, so it's the genuinely-testable slice of that vision
to build now; the rest stays on the deferred list.

- **`imports/svg_import.py`**: `import_svg(svg_content) -> (entities,
  warnings)` walks the XML tree with `xml.etree.ElementTree` and converts a
  deliberately constrained element subset — `line`, `circle`, `ellipse`,
  `rect`, `polyline`, `polygon`, `text`, and `path` restricted to the
  straight-segment commands `M`/`L`/`H`/`V`/`Z` (both absolute and
  relative) — into the same `Entity` models every other pipeline stage
  uses. Curve commands (`C`/`S`/`Q`/`T`/`A`) are detected up front per
  `<path>` and that path is skipped with a warning rather than
  approximated with line segments, which would silently misrepresent the
  original shape. Unrecognized elements (anything not in the list above,
  e.g. `<foo>`, or a future `<image>`) produce a warning and are skipped;
  purely structural elements (`<svg>`, `<g>`, `<defs>`, `<title>`, `<desc>`,
  `<metadata>`, `<style>`) are silently ignored since they carry no
  geometry of their own here.
- **Coordinate system**: SVG's y-axis points down; CAD's points up. Every
  coordinate is flipped against the document height, read from `viewBox`
  first and the `height` attribute second; if neither is present the code
  falls back to a plain sign flip (no reference height needed for the
  shape to still come out right-side-up, just an arbitrary vertical
  offset).
- **Deliberately out of scope**: `<g>`/element `transform` attributes
  (translate/rotate/scale/matrix) are not applied — a transformed group's
  children import at their raw local coordinates. CSS/inline styling
  (`fill`, `stroke`, class-based styles) is not mapped to CAD color/
  lineweight; only an element's `id` attribute becomes its imported
  `layer`. These are real gaps for anything beyond simple, ungrouped,
  unstyled vector art, and are noted rather than silently producing wrong
  geometry.
- **Security**: SVG is XML, and XML entity expansion (billion-laughs) and
  external entity resolution (XXE) are known attack classes for any format
  built on it. `import_svg` rejects any document containing a `DOCTYPE` or
  `ENTITY` declaration outright, and caps input size (2MB) before parsing
  — cheap, defensible guards appropriate for a REST/MCP endpoint that
  accepts arbitrary text from a caller.
- **`import_svg` tool**: takes `svg_content` (+ optional `layer`/`color`
  overrides applied to every imported entity), routes the resulting
  multi-entity plan through the same `run_pipeline`/`result_entries` path
  `insert_symbol` and `load_project` use, and returns per-import
  `import_warnings` alongside the usual validation `warnings`/`autofixed`
  — so a caller can tell "imported but I skipped 2 curve paths" apart from
  a hard failure. No dedicated REST route was added; it's reachable at the
  existing generic `POST /tools/import_svg`, same as most other tools.
- **Dashboard "Import SVG" panel**: a textarea for pasting raw SVG plus an
  optional layer override, wired through the shared `handleToolResult()`
  so imported entities flow into the same live preview as everything else;
  per-element `import_warnings` are logged individually. Verified live via
  Playwright: pasting an SVG with a rect, a circle, and a curved path drew
  the rect and circle into the preview at the correct flipped positions
  and logged the curved path as skipped.

## Phase 12: Execution log (dashboard "Logs" section)

The master vision's dashboard has a dedicated Logs section; this phase
builds the real thing behind it rather than a placeholder, using the same
"single cross-cutting concern, applied once" approach Phase 9's plugin
loading and Phase 7's render toggle already established.

- **`apps/execution_log.py`**: `ExecutionLog` is a `deque(maxlen=500)`-backed
  ring buffer of `ExecutionLogEntry` (seq, tool, success, message,
  duration_ms, timestamp). Process-lifetime only, like `ServerContext
  .history` — no persistence, no cross-restart durability, and bounded so
  a long session can't grow it without limit.
- **How every call gets recorded without either transport knowing**: both
  `apps/server/server.py` (MCP) and `apps/api/main.py` (REST, including
  every "sugar" route like `GET /symbols` that calls a tool handler
  directly rather than through the generic `POST /tools/{name}`) invoke
  `ToolSpec.handler` the same way — by reference, off `TOOLS_BY_NAME`/
  `TOOL_REGISTRY`. So `apps/server/tools.py` wraps every handler exactly
  once, at module import time, with `_with_execution_logging`, timing the
  call and recording `result["success"]`/`result["message"]` afterward.
  Neither transport module changed at all; this is the same trick that
  made `run_pipeline` a single source of truth, applied one layer up.
- **`get_execution_log`/`clear_execution_log` tools + `GET /logs`,
  `POST /logs/clear`**: `get_execution_log` itself is logged too (it's a
  tool call like any other) — a real audit trail records reads of itself,
  which is correct behavior, not a bug, and is covered by a test.
- **Dashboard "Logs" panel**: lists recent entries (tool, duration,
  timestamp, message), color-coded success/failure via the same
  `.entry.success`/`.entry.failure` classes the other panels already use.
  `handleToolResult()` (the function every chat/tool-caller/symbol/SVG-
  import action already funnels through) now also calls `refreshLogs()`,
  so the panel updates live after most actions with no per-panel wiring;
  the three action paths that don't go through `handleToolResult` (save/
  clear-drawing, export, and the Projects panel) each got one explicit
  `refreshLogs()` call added alongside their existing logging. Verified
  live with Playwright: a successful `process_command` and a deliberately
  invalid `draw_circle` (negative radius) both showed up correctly
  color-coded with their real messages, and Clear emptied the panel.

**Scope boundary, stated plainly**: the import-time wrapping only covers
tools present in `TOOL_REGISTRY` at the moment `apps/server/tools.py`
finishes loading. Plugin-contributed tools (Phase 9) are appended to that
same list later, inside `build_context()`, well after this module has
already finished wrapping — so a plugin's own tool calls are *not*
recorded in the execution log. Fixing this generally (e.g. a wrap-hook
plumbed through `plugins/loader.py`) was judged not worth the added
indirection for a gap this narrow; it's noted here in the same spirit as
the other two documented plugin-loading-order caveats (Phase 9's
`ValidationEngine` rule-snapshot timing, and plugin backends not being
selectable as the default session backend).

## Phase 13: Performance (dashboard "Performance" section)

A direct extension of Phase 12 rather than new infrastructure: the
execution log already records a duration and success flag for every call,
so a "Performance" dashboard section is a pure aggregation over data that
already exists, with zero new state to manage.

- **`ExecutionLog.stats()`**: groups the log's current entries by tool
  name and computes call count, success/failure counts, and avg/min/max
  `duration_ms` per tool, sorted by call count descending (most-called
  first — the natural "what's hot" read order). Returns `ToolStats`
  dataclass instances, mirroring `ExecutionLogEntry`'s style.
- **Same bounded-window caveat as `recent()`**: `stats()` aggregates
  whatever is currently in the `deque(maxlen=500)`, not a true cumulative
  historical metric — once eviction starts dropping old entries, a tool
  that was called many times early in a long session will show a lower
  count than it actually accumulated. This is stated plainly rather than
  quietly presented as a real metrics store (no Prometheus-style counters,
  no persistence, no export format).
- **`get_performance_stats` tool + `GET /performance`**: returns per-tool
  stats plus `total_calls`/`overall_success_rate` across all tools. Added
  to `TOOL_REGISTRY` before the Phase 12 logging-wrap loop runs, so its
  own calls are logged too, same as `get_execution_log`.
- **Dashboard "Performance" panel**: a table (tool, calls, OK, fail, avg/
  min/max ms) plus a one-line summary. Rather than wiring a third refresh
  call through every action path, `refreshLogs()` itself now also calls
  `refreshPerformance()` — both panels read from the same underlying log,
  so one refresh trigger keeps both current with no per-panel plumbing.
  Verified live via Playwright: a mix of successful `process_command`
  calls and one deliberately invalid `draw_circle` produced a table with
  correct per-tool counts (`draw_circle` showing 0 successes / 1 failure)
  and a correct overall success rate.

## Phase 14: Settings (dashboard "Settings" section)

The simplest of the three observability-dashboard phases: there was
already a single validated `Settings` object (`config.py`, Phase 5)
resolved once at process startup — this phase just makes it visible
rather than buried in whatever `config.json`/env vars produced it.

- **`ServerContext.settings`**: the resolved `Settings` instance is now
  threaded through `build_context()` onto the context, alongside the
  pieces already derived from it (`backend`, `project_store`). Defaults
  to `field(default_factory=Settings)` so every existing test fixture
  that constructs a bare `ServerContext(...)` without a `settings=` kwarg
  keeps working unchanged — same pattern `history`/`execution_log` used.
- **`get_settings` tool + `GET /settings`**: returns `ctx.settings
  .model_dump()` as-is. Nothing in `Settings` is a secret (backend name,
  local directory paths, server name/version) so no redaction logic was
  needed — worth noting explicitly, since a settings-exposure endpoint is
  exactly the kind of thing that *would* need redaction if a future
  setting held credentials.
- **Read-only, deliberately no live-editable settings API**: `cad.backend`
  is already baked into the constructed `CADBackend` instance by the time
  `get_settings` could return it, so exposing a `PATCH /settings` that
  "changed" the backend would just be misleading — it can't take effect
  without reconstructing the whole `ServerContext`. Changing any setting
  still means editing `config.json`/env vars and restarting, same as
  before this phase; this just makes the *current* resolved values
  visible instead of requiring the operator to go find the file.
- **Dashboard "Settings" panel**: a read-only formatted JSON view, loaded
  once at `init()` — no refresh button, since nothing here can change
  without a process restart (unlike Logs/Performance, which are legitimately
  live). Verified live via Playwright: the panel showed the real resolved
  `cad.backend`, `output.directory`, `storage.directory`, and
  `plugins.directory` values.

## Phase 15: Templates (dashboard "Templates" section)

Distinct from Phase 10's symbol library on purpose: a symbol is a small
reusable component inserted at a point (a resistor, a door swing); a
template is a whole-sheet layout a drawing gets built on top of (a
border plus a title block). Conflating the two under "symbols" would
have muddied both.

- **`templates/library.py`**: `build_title_block(template_name, title=,
  drawn_by=, date=, scale=, sheet_number=, origin=)` builds a border
  rectangle plus a nested title-block box in the bottom-right corner,
  divided into a 2-column-by-3-row grid (Title spans the top row; Drawn
  by/Date and Scale/Sheet split the middle and bottom rows). Every text
  field is optional — an empty string just omits that label, so a caller
  can insert a bare border-and-grid with no placeholder text. Sheet sizes
  (`a4_landscape`, `a3_landscape`, `letter_landscape`) are the
  **public-domain ISO 216/ANSI paper dimensions themselves** — plain
  numbers, not licensed content — which is a different, much smaller
  claim than a real title-block *standard* (exact zone layout, field
  codes, revision-table format per ISO 7200 or a company's drafting
  standard), which is not attempted here for the same licensing reason
  Phase 10's symbols aren't ANSI/IEC-compliant.
- **A real bug, caught by testing the dashboard, not by unit tests
  alone**: the first implementation's `_shift()` helper indexed
  `origin[2]` unconditionally, but REST/MCP tool arguments arrive as a
  plain `[x, y]` JSON list (no `z`) — unlike a `Point3` field inside a
  validated `Entity`, which pads a missing `z` via `_coerce_point`'s
  `BeforeValidator`. All of `templates/library.py`'s own unit tests
  passed (they all called `build_title_block` with proper 3-tuples), so
  this only surfaced as a live `IndexError` → 500 when the dashboard's
  "Insert" button sent a real `[0, 0]` origin. Fixed by normalizing
  `origin` to a 3-tuple once at the top of `build_title_block`, the same
  defensive pattern `symbols/library.py`'s `_transform` already used for
  exactly this reason — a pattern this phase should have reused
  up front rather than rediscovering the hard way. A regression test
  (`test_two_element_origin_defaults_z_to_zero`) now covers it directly.
- **`list_templates`/`insert_title_block` tools + `GET /templates`,
  `GET /templates/{name}/preview`**: same shape as Phase 10's symbol
  tools — `insert_title_block` is multi-entity, so it goes through
  `run_pipeline`/`result_entries` directly, and the preview endpoint
  reuses `export.renderer` with sample field values, no template-specific
  rendering code.
- **Expected noise, not a bug**: inserting a title block produces several
  `possible_collision` validator warnings (Phase 3's bounding-box-overlap
  heuristic), because a title block is inherently nested geometry — the
  outer border's bounding box contains the title-block box's, which
  contains its divider lines' and text's. This is a pre-existing rule
  applied to a new, legitimately-nested use case, not something Phase 15
  introduced or worsened; fixing the heuristic itself (distinguishing
  "genuinely overlapping duplicate geometry" from "intentionally nested
  geometry") would mean touching the shared validator every other tool
  also depends on, which is out of scope for a templates feature. The
  operation still succeeds — these are warnings, not errors.
- **Dashboard "Templates" panel**: shared title/drawn_by/date/scale/
  sheet_number/origin inputs applied to whichever sheet size's Insert
  button is clicked, plus live SVG thumbnails via the preview endpoint —
  same interaction pattern as the Symbols panel. Verified live via
  Playwright, including reproducing and then confirming the fix for the
  origin bug above.

## Phase 16: Execution Queue (dashboard "Execution Queue" section)

The last of the master vision's dashboard sections. Genuinely distinct
from `/drawings/execute` (Phase 4's atomic, single-`DrawingPlan`,
validate-or-nothing batch execute) rather than a thin wrapper around it:
a queue holds a sequence of *independent tool calls* — not necessarily
geometry at all, could be a `create_layer` then a few `draw_*` calls then
a `save_drawing` — enqueued without running, inspectable and
individually removable, then run as a batch where **one item's failure
doesn't stop the rest**. That partial-failure tolerance is the actual
feature; without it this would just be `/drawings/execute` with extra
steps.

- **`apps/execution_queue.py`**: `ExecutionQueue` is a plain ordered list
  of `QueueItem` (id, tool, arguments, enqueued_at, status, result).
  `enqueue()`/`items()`/`get()`/`remove()`/`clear()` — no execution logic
  here at all; this module only holds state, same separation `history`
  and `execution_log` keep between "data" and "what acts on it."
- **`run_queue`'s actual behavior**: iterates every item still in
  `"queued"` status, looks the tool up in `TOOLS_BY_NAME`, calls
  `tool.handler(item.arguments, ctx)` — the same wrapped handler either
  transport calls — and records `succeeded`/`failed` plus the tool's own
  result on the item itself, continuing regardless of that item's
  outcome. Already-run items are skipped on a second `run_queue` call
  (their status is no longer `"queued"`), so re-running is safe and
  idempotent rather than re-executing everything. Because this reuses the
  already-wrapped `tool.handler`, every queued tool call that actually
  runs is *also* recorded in `execution_log`/visible in `Performance` —
  no separate logging path needed.
- **5 tools**: `enqueue_operation`, `get_queue`, `remove_queue_item`,
  `run_queue`, `clear_queue` — REST sugar at `GET/POST /queue`,
  `DELETE /queue/{id}`, `POST /queue/run`, `POST /queue/clear`.
- **Scope, stated plainly**: this is *not* real concurrent job
  processing. "Running the queue" executes items synchronously, one after
  another, inside the same HTTP request/MCP tool call — there is no
  background worker, no parallelism, no persistence across a restart.
  For a single-backend-document, single-process platform (see the Phase
  6 persistence-scope note), that is an honest reflection of what this
  system can actually do, not a simplification of some larger queuing
  system that was cut for time.
- **Dashboard**: an "Enqueue instead" button added next to the existing
  "Call tool" button in the Tools panel (same tool-select/args, one new
  request path), plus a new "Execution Queue" panel listing items
  color-coded by status with a per-item Remove button (shown only while
  still queued) and Run Queue/Clear controls. Verified live via
  Playwright: enqueuing a valid `draw_circle` and an invalid one (negative
  radius), running the queue, and confirming the first item drew into the
  live preview while the second showed its real validation error and
  neither blocked the other; then confirmed Remove and Clear.

## Phase 17: More symbol disciplines (mechanical, HVAC, structural)

The lowest-risk item left on the deferred list: Phase 10 already proved
the symbol pattern generalizes, so this phase is "more of them," not new
architecture — 6 symbols across 3 new disciplines (mechanical: `bearing`,
`weld_symbol`; HVAC: `diffuser`, `thermostat`; structural: `column`,
`beam`), bringing the catalog to 15.

- **Zero changes needed outside `symbols/library.py` and its tests**:
  `list_symbols`/`insert_symbol`, `GET /symbols`, `GET /symbols/{name}
  /preview`, and the dashboard's Symbols panel all already iterate
  `SYMBOL_LIBRARY` generically rather than hardcoding symbol names —
  confirmed live via Playwright, where all 15 symbols (old and new)
  appeared in the grid the moment the catalog gained entries, with no
  dashboard code touched.
- **First symbol built from a `TextEntity`**: every prior symbol used
  only `LineEntity`/`CircleEntity`/`ArcEntity`/`PolylineEntity`.
  `thermostat` needed a "T" label, so a new `_text()` helper was added
  alongside `_line()`/`_circle()`/`_arc()`, transforming a text position
  through the same `_transform()` scale-rotate-translate pipeline and
  scaling the text height by the symbol's `scale` argument (verified by
  a dedicated test and visually via PNG render — the letter appears
  centered and legible inside the thermostat's circle).
- Each new symbol was rendered to PNG and visually inspected before
  being committed, same discipline as Phase 10: `bearing` (concentric
  circles), `weld_symbol` (reference line + fillet triangle), `diffuser`
  (square with an X — distinct from `column`'s nested-squares so the two
  don't read as the same shape), `thermostat` (circle + "T"), `column`
  (nested squares), `beam` (I-beam cross-section: two flanges + a web).
- Same scope boundary as Phase 10, restated rather than re-litigated:
  these are illustrative, recognizable symbols, not verified against
  ASME Y14.5, ISO 14617, or any other licensed standard's exact
  proportions or line weights.

## What is still deferred (not stubbed)

The following from the master vision are **not** built yet, and no
placeholder directories were created for them (an empty `dashboard`
section folder communicates nothing and just adds noise). All of the
master vision's originally-named dashboard sections now exist in some
form (Phases 12–16) — see each phase's scope-boundary note above for
what's still shallow about them: Logs is a flat recent-calls list rather
than queryable/filterable, Performance aggregates only the same bounded
in-memory window rather than a true cumulative historical metric,
Settings is read-only with no live-editable API, Templates is one
starter layout (a title block) rather than a general reusable-fragment
system, and Execution Queue runs items synchronously in-request rather
than via a real background worker.

- Multi-format import beyond SVG: PDF, raster image, hand sketch,
  Excel/CSV, flowcharts — these need OCR/ML or heavyweight parsing this
  sandbox can't install or verify (Phase 11 covers plain, unstyled,
  ungrouped SVG; `<g>`/element transforms and CSS/fill/stroke-to-CAD-color
  mapping are gaps even within SVG — see the Phase 11 scope boundary above)
- The ANSI/ISO/IEC/ASME/DIN/JIS standards knowledge base itself (dimension
  rules, title block *standards* — as opposed to Phase 15's plain paper
  dimensions — named-standard layer conventions) — the symbol *library*
  now exists (Phase 10), but it is illustrative geometry,
  not licensed standards content
- Additional symbol disciplines beyond electrical/piping/architectural/
  mechanical/HVAC/structural (Phase 17 added the latter three) — hydraulic,
  pneumatic, civil, instrumentation, networking, industrial automation,
  warehouse, manufacturing remain unbuilt — same pattern as Phases 10 and
  17, just more of them
- DWG export and hatch support in `.scr`/`.lsp` (DXF, SVG, PNG, SCR, and
  LISP all work now for non-hatch geometry)
- FreeCAD/Fusion/SolidWorks/Revit backends (the `CADBackend` interface is
  designed so these can be added later as new adapters)
- True multi-document/multi-tenant project isolation (see the Phase 6 note above)
- Plugin-provided CAD backends as the selectable default session backend
  (see the Phase 9 scope boundary above)

These become straightforward additions once the spine under them is proven
— building them before that spine exists would mean rewriting them anyway.
