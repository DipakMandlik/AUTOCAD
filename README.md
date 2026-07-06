# CAD MCP Platform

An AI CAD engineering platform: natural language and typed tool calls flow
through a planning → validation → execution pipeline before anything is
drawn, exposed both to LLM clients (Claude Desktop, Cursor, MCP Inspector)
over the Model Context Protocol and to any HTTP client over a REST API.

This repository builds on [daobataotie/CAD-MCP](https://github.com/daobataotie/CAD-MCP)
as a ground-up redesign. See:

- [`docs/architecture-review.md`](docs/architecture-review.md) — analysis of the original repository
- [`docs/architecture.md`](docs/architecture.md) — the redesigned architecture and rationale

## Current scope

The core is a fully working, tested planning → geometry → validation →
execution pipeline with a headless backend, exposed over MCP, a REST API,
and a small web dashboard. This is several phases into a larger roadmap
(see "What's deferred" below) — each phase deliberately scoped to what's
genuinely usable and testable now, rather than a shallow pass over every
feature in the platform vision.

```
MCP tool call (typed args)         process_command (natural language)
        │                                     │
        └──────────────┬──────────────────────┘
                        ▼
                  engine/planner        (text/args -> typed DrawingPlan)
                        ▼
              engine/geometry           (derived geometry, bounding boxes)
                        ▼
              engine/validator          (checks + safe autofixes)
                        ▼
                   cad/backend          (DXFBackend or AutoCADBackend)
```

## Requirements

- Python 3.10+
- The `dxf` backend (default) runs anywhere — Linux, macOS, Windows, CI —
  with no CAD software installed.
- The `autocad`/`gcad`/`gstarcad`/`zwcad` backends require Windows,
  `pywin32`, and a licensed, installed copy of AutoCAD, GstarCAD, or ZWCAD.
  **This backend has not been exercised in this environment** (no
  Windows/CAD available where it was written) — verify it against a real
  install before relying on it.

## Install

```bash
pip install -e ".[dev]"          # core + pytest/ruff/matplotlib
pip install -e ".[autocad]"      # adds pywin32, Windows only
pip install -e ".[render-png]"   # adds matplotlib for PNG rendering (SVG needs no extra)
```

## Run the tests

```bash
pytest -v      # 213 tests, all run headlessly against the DXF backend
ruff check .
```

## Plugins

Drop a `.py` file defining a module-level `PLUGIN` object into the
configured plugins directory (`plugins.directory` in config, default
`./plugins_installed`) and the platform picks it up at startup — no core
file needs to change. A plugin can contribute new MCP/REST tools, new
validation rules, and new CAD backends. See
[`examples/plugins/example_plugin.py`](examples/plugins/example_plugin.py)
for a complete, runnable example (a `draw_regular_polygon` tool plus a
custom validation rule), and `docs/architecture.md` (Phase 9) for how
discovery works and a real scope boundary: a plugin-registered backend
can't currently be selected as the *default* session backend via the
`cad.backend` config field (config is validated before plugins load).

```bash
mkdir -p plugins_installed
cp examples/plugins/example_plugin.py plugins_installed/
uvicorn apps.api.main:app --reload   # draw_regular_polygon now appears in GET /tools
```

## Run the REST API + dashboard

```bash
uvicorn apps.api.main:app --reload
```

Then open `http://localhost:8000/dashboard/` for the web UI (AI chat, a
tool explorer with live schemas, an SVG drawing preview with an
accurate-render toggle, .scr/.lsp download buttons, a validate-only
dry-run panel, and a Projects panel for saving/loading drawings), or use
the API directly:

- `GET /health` — status + which backend is active
- `GET /tools` — list every tool's name/description/JSON schema
- `POST /tools/{name}` — call a tool (same handlers and pipeline as MCP)
- `POST /drawings/validate` — validate a full `DrawingPlan` without executing
- `POST /drawings/execute` — execute a multi-entity `DrawingPlan` in one call
- `GET /drawings/current` / `POST /drawings/clear` — the session's drawing history
- `GET /drawings/current/render?format=svg|png` — a real, CAD-accurate render (not the dashboard's coarse client-side preview)
- `GET/POST /projects`, `GET /projects/{id}` — list/create/inspect saved projects
- `POST /projects/{id}/revisions` — snapshot the current drawing as a new revision
- `POST /projects/{id}/load` — re-draw a saved project's plan
- `GET /projects/{id}/render?format=svg|png` — render a saved project's plan
- `GET /drawings/current/export?format=scr|lisp` — download an AutoCAD Script or AutoLISP file (hatch entities are skipped, see below)
- `GET /projects/{id}/export?format=scr|lisp` — same, for a saved project
- `GET /symbols` — list the built-in symbol library (name/discipline/description)
- `GET /symbols/{name}/preview?format=svg|png` — render a symbol in isolation, for the dashboard's symbol grid

```bash
curl -X POST localhost:8000/tools/draw_circle -H 'content-type: application/json' \
  -d '{"center": [0, 0], "radius": 10}'

curl -X POST localhost:8000/drawings/execute -H 'content-type: application/json' \
  -d '{"operations": [{"type": "line", "start": [0,0], "end": [10,10]}]}'

curl -X POST localhost:8000/projects -H 'content-type: application/json' \
  -d '{"name": "my-drawing"}'

curl localhost:8000/drawings/current/render?format=svg -o drawing.svg
curl localhost:8000/drawings/current/export?format=scr -o drawing.scr

curl -X POST localhost:8000/tools/import_svg -H 'content-type: application/json' \
  -d '{"svg_content": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 10 10\"><circle cx=\"5\" cy=\"5\" r=\"2\"/></svg>"}'
```

## Run the MCP server

```bash
python -m apps.server.server
```

By default this uses the `dxf` backend and writes output under `./output`.
Configure via an optional `config.json` in the working directory, or
`CADMCP_*` environment variables (nested fields use `__`, e.g.
`CADMCP_CAD__BACKEND=autocad`):

```json
{
  "server": { "name": "CAD MCP Server", "version": "2.0.0" },
  "cad": { "backend": "dxf", "startup_wait_time": 20.0 },
  "output": { "directory": "./output", "default_filename": "cad_drawing.dxf" }
}
```

`cad.backend` selects the execution target: `dxf` (default, headless),
`autocad`, `gcad`, `gstarcad`, or `zwcad`.

### Available tools

`draw_line`, `draw_circle`, `draw_arc`, `draw_ellipse`, `draw_polyline`,
`draw_rectangle`, `draw_text`, `draw_hatch`, `add_dimension`,
`save_drawing`, `create_layer`, `process_command` (natural language),
`get_current_drawing`, `clear_current_drawing`, `render_current_drawing`,
`export_script`, `export_lisp`, `create_project`, `list_projects`,
`get_project`, `snapshot_project`, `load_project`, `list_symbols`,
`insert_symbol`, `import_svg`.

`import_svg` accepts a raw SVG document and converts a constrained element
subset (`line`/`circle`/`ellipse`/`rect`/`polyline`/`polygon`/`text`, and
`path` restricted to straight-segment commands `M`/`L`/`H`/`V`/`Z`) into
drawing entities — curved path commands and any other unsupported element
are skipped with a warning rather than approximated, and `<g>`/element
`transform` attributes and CSS styling aren't applied (see
`docs/architecture.md` Phase 11). There's no dedicated REST route for it;
it's reachable at the generic `POST /tools/import_svg`, same as most tools.

`export_script`/`export_lisp` generate an AutoCAD Script (.scr) or
AutoLISP (.lsp) file — the one path to real AutoCAD that needs neither
Windows nor `pywin32`: run the script via AutoCAD's `SCRIPT` command, or
load the LISP file via `APPLOAD`. **Hatch entities are skipped** (not
guessed at — see `docs/architecture.md` Phase 8), and like the `autocad`
backend, **the generated commands are unverified against a real AutoCAD
install**.

Every geometry tool argument matches the field names in
`engine/geometry/primitives.py` directly (e.g. `start`/`end` for a line,
`center`/`radius` for a circle) — this is an intentional change from the
original repo's tool schema, which used different names in different
places; one shared vocabulary end-to-end removes a translation layer.

## Module map

See [`docs/architecture.md`](docs/architecture.md) for the full rationale.
Briefly:

- `engine/geometry/` — entity models (`DrawingPlan` and friends) and derived-geometry computation
- `engine/planner/` — intent detection + turns intent into a `DrawingPlan`
- `engine/validator/` — structural checks and safe autofixes
- `nlp/` — offline regex/keyword fallback intent source (not an LLM)
- `cad/` — the `CADBackend` interface and backend registry
- `dxf/` — headless backend (ezdxf); what the test suite runs against
- `autocad/` — Windows COM backend for AutoCAD/GstarCAD/ZWCAD
- `storage/` — `Project`/`Revision` models + file-based `ProjectStore` (one JSON document per project)
- `export/` — `render_svg`/`render_png` (real, CAD-accurate rendering via ezdxf's drawing addon) and `render_scr`/`render_lisp` (AutoCAD Script / AutoLISP generation, unverified)
- `plugins/` — the Plugin SDK: `Plugin` data shape + file-based discovery/apply
- `examples/plugins/` — a complete, runnable example plugin
- `symbols/` — the built-in engineering symbol library (electrical/piping/architectural), `insert_symbol`/`list_symbols` tools sit on top
- `imports/` — `svg_import.py`, a constrained SVG-to-`DrawingPlan` parser; the `import_svg` tool sits on top
- `apps/context.py` — shared `ServerContext` wiring used by every app below
- `apps/server/` — the MCP stdio server and its tool registry
- `apps/api/` — the REST API (same tool registry, second transport)
- `apps/dashboard/` — static web UI served by the REST API at `/dashboard`
- `config.py` — single validated settings source

Note on persistence scope: there is still exactly one live backend document
per process. Saving/loading projects snapshots and restores a `DrawingPlan`,
but "loading" a project draws it into whatever is currently open rather
than opening it in an isolated document — see `docs/architecture.md`
(Phase 6) for why full multi-document isolation is a bigger change than
this pass attempted.

## What's deferred

Per the master platform vision, not built yet (see `docs/architecture.md`
for why): dashboard sections that need richer UI/backend support
(Templates, Execution Queue, Logs, Performance, Settings); multi-format
import beyond SVG (PDF/image/sketch/Excel — these need OCR/ML or
heavyweight parsing this sandbox can't install or verify; `import_svg`,
see "Available tools" above, covers plain, unstyled, ungrouped SVG only —
`<g>`/element transforms and CSS fill/stroke-to-CAD-color mapping are
gaps even within SVG); the ANSI/ISO/IEC/ASME/DIN/JIS standards
knowledge base itself and symbol disciplines beyond electrical/piping/
architectural (a starter symbol library across those three disciplines
now exists — `symbols/`, see "Available tools" above — but the symbols
are illustrative/recognizable, not verified against any standard's exact
line weights, proportions, or annotation conventions); DWG export and
hatch support in .scr/.lsp (DXF, SVG, PNG, SCR, and LISP all work now for
non-hatch geometry); non-AutoCAD-family backends (FreeCAD, Fusion 360,
etc.); true multi-document/multi-tenant project isolation; and
plugin-provided CAD backends as the selectable default session backend
(a plugin backend works fine when a plugin's own tools call it directly).
The `CADBackend` interface is designed so new backends can be added later
without touching the planning/validation spine. MCP resources and prompts
(`drawing://current`, the `cad-assistant` prompt) from the original repo
were also not carried over as MCP-native resources — `get_current_drawing`
is the equivalent capability, exposed as a tool instead.
