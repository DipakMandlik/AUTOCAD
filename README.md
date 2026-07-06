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
pytest -v      # 106 tests, all run headlessly against the DXF backend
ruff check .
```

## Run the REST API + dashboard

```bash
uvicorn apps.api.main:app --reload
```

Then open `http://localhost:8000/dashboard/` for the web UI (AI chat, a
tool explorer with live schemas, an SVG drawing preview, a validate-only
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

```bash
curl -X POST localhost:8000/tools/draw_circle -H 'content-type: application/json' \
  -d '{"center": [0, 0], "radius": 10}'

curl -X POST localhost:8000/drawings/execute -H 'content-type: application/json' \
  -d '{"operations": [{"type": "line", "start": [0,0], "end": [10,10]}]}'

curl -X POST localhost:8000/projects -H 'content-type: application/json' \
  -d '{"name": "my-drawing"}'

curl localhost:8000/drawings/current/render?format=svg -o drawing.svg
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
`create_project`, `list_projects`, `get_project`, `snapshot_project`,
`load_project`.

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
- `export/` — `render_svg`/`render_png`: real, CAD-accurate rendering via ezdxf's drawing addon
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
for why): plugin SDK; the dashboard sections that need a plugin SDK or
richer UI first (Templates, Symbol Libraries, Execution Queue, Logs,
Performance, Settings); multi-format import (PDF/image/sketch/Excel);
symbol libraries and the ANSI/ISO/IEC standards knowledge base; DWG/LISP/SCR
export (DXF, SVG, and PNG work now); non-AutoCAD-family backends (FreeCAD,
Fusion 360, etc.); and true multi-document/multi-tenant project isolation.
The `CADBackend` interface is designed so new backends can be added later
without touching the planning/validation spine. MCP resources and prompts
(`drawing://current`, the `cad-assistant` prompt) from the original repo
were also not carried over as MCP-native resources — `get_current_drawing`
is the equivalent capability, exposed as a tool instead.
