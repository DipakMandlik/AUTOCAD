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

This is the **core framework MVP**: a fully working, tested planning →
geometry → validation → execution pipeline with a headless backend. It is
one phase of a larger roadmap (see "What's deferred" below) — deliberately
scoped down rather than a shallow pass over every feature in the platform
vision.

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
pip install -e ".[dev]"          # core + pytest/ruff
pip install -e ".[autocad]"      # adds pywin32, Windows only
```

## Run the tests

```bash
pytest -v      # 69 tests, all run headlessly against the DXF backend
ruff check .
```

## Run the REST API

```bash
uvicorn apps.api.main:app --reload
```

- `GET /health` — status + which backend is active
- `GET /tools` — list every tool's name/description/JSON schema
- `POST /tools/{name}` — call a tool (same handlers and pipeline as MCP)
- `POST /drawings/validate` — validate a full `DrawingPlan` without executing
- `POST /drawings/execute` — execute a multi-entity `DrawingPlan` in one call

```bash
curl -X POST localhost:8000/tools/draw_circle -H 'content-type: application/json' \
  -d '{"center": [0, 0], "radius": 10}'

curl -X POST localhost:8000/drawings/execute -H 'content-type: application/json' \
  -d '{"operations": [{"type": "line", "start": [0,0], "end": [10,10]}]}'
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
`save_drawing`, `create_layer`, `process_command` (natural language).

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
- `apps/context.py` — shared `ServerContext` wiring used by both apps below
- `apps/server/` — the MCP stdio server and its tool registry
- `apps/api/` — the REST API (same tool registry, second transport)
- `config.py` — single validated settings source

## What's deferred

Per the master platform vision, not built yet (see `docs/architecture.md`
for why): dashboard, plugin SDK, multi-format import (PDF/image/sketch/
Excel), symbol libraries and the ANSI/ISO/IEC standards knowledge base,
DWG/SVG/PDF/LISP/SCR export, non-AutoCAD-family backends (FreeCAD, Fusion
360, etc.), and a persistence/project/revision-history layer. The
`CADBackend` interface is designed so new backends can be added later
without touching the planning/validation spine. MCP resources and prompts
(`drawing://current`, the `cad-assistant` prompt) from the original repo
were also not carried over.
