# MASTER PROMPT — Local Setup + Real AutoCAD Verification

## Context

This repo (`cad-mcp-platform`) is an AI CAD engineering platform: natural
language and typed tool calls flow through a planning → validation →
execution pipeline, exposed over MCP and a REST API, with a small web
dashboard. It was built and tested across 17 phases entirely on a Linux
sandbox, against the `dxf` backend (`ezdxf`, fully cross-platform, no CAD
software required). See `docs/architecture.md` and `README.md` for the
full history and scope.

**The one thing that has never been run against real software is
`autocad/backend.py`** — the Windows COM automation driver for AutoCAD,
GstarCAD, and ZWCAD. It was written carefully (fixing several real bugs
found in the original repo it's based on) and is believed correct by
inspection, but it has genuinely never touched a live AutoCAD install.
This prompt walks through (1) confirming the baseline still works on
your Mac, and (2) the separate Windows-only steps needed to actually
exercise the AutoCAD backend for real.

---

## Part 1 — Clone and verify on your Mac (everything except AutoCAD)

```bash
git clone https://github.com/DipakMandlik/AUTOCAD.git
cd AUTOCAD
git checkout main        # or claude/session-5hm61a for the exact dev branch

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest -v                # expect 294 passed, 0 failed
ruff check .             # expect no errors
```

Then run the REST API + dashboard — this is the `dxf` backend and is
fully native on macOS, no AutoCAD needed:

```bash
uvicorn apps.api.main:app --reload
```

Open `http://localhost:8000/dashboard/` and try drawing shapes,
inserting symbols/templates, importing SVG, using the execution queue —
all of it works end to end on your Mac as-is.

You can also run the MCP stdio server directly and point Claude
Desktop/Cursor/MCP Inspector at it:

```bash
python -m apps.server.server
```

If anything in this part fails on your Mac, that's a real regression —
worth fixing before moving on to AutoCAD.

---

## Part 2 — IMPORTANT: AutoCAD automation requires Windows, not macOS

This is the critical fact to not gloss over: `autocad/backend.py` drives
AutoCAD via **COM automation** (`pywin32`/`win32com`), which is a
Windows-only technology (part of the Windows Component Object Model).

**AutoCAD for Mac cannot be used to test this backend, even if you own a
Mac AutoCAD license.** Autodesk's Mac build has a completely different,
much more limited automation surface — no VBA, no COM/ActiveX, nothing
equivalent to `win32com`'s object model. There is no code change that
makes this backend work on macOS; the only way to genuinely exercise it
is a real Windows environment with AutoCAD (or GstarCAD/ZWCAD) installed.

Your options, roughly in order of convenience:

1. A physical or work-provided Windows PC/laptop with AutoCAD already
   licensed and installed.
2. Windows in a VM on your Mac — Parallels Desktop or VMware Fusion.
   (Apple Silicon Macs need the ARM64 Windows build; Intel Macs can also
   use Boot Camp.) AutoCAD is licensed and installed inside that VM like
   any normal Windows machine.
3. A cloud Windows VM (Azure/AWS/GCP Windows Server, or a service like
   Shadow/Paperspace) with AutoCAD installed, accessed via RDP.

There's no way around actually running Windows + a real AutoCAD-family
install somewhere — that's a limitation of AutoCAD's own architecture,
not of this repo.

---

## Part 3 — On the Windows machine, once AutoCAD is installed

```bat
git clone https://github.com/DipakMandlik/AUTOCAD.git
cd AUTOCAD
git checkout main

python -m venv .venv
.venv\Scripts\activate
pip install -e ".[autocad]"
```

`.[autocad]` adds `pywin32` on top of the base dependencies.

**Launch AutoCAD manually first** and let it fully load before running
anything — COM automation is far more reliable attaching to an
*already-running* instance than cold-launching one itself.

Create a `config.json` in the repo root:

```json
{
  "cad": { "backend": "autocad", "startup_wait_time": 20.0 },
  "output": { "directory": "./output" }
}
```

- `backend` must be one of: `autocad` (real AutoCAD, COM ProgID
  `AutoCAD.Application`), `gcad`/`gstarcad` (GstarCAD, ProgID
  `GCAD.Application`), or `zwcad` (ZWCAD, ProgID `ZWCAD.Application`) —
  whichever you actually have installed (see `autocad/backend.py`'s
  `APP_IDS` table).
- `startup_wait_time` only matters if AutoCAD *isn't* already running
  when the backend starts — it waits this many seconds after launching
  before touching the COM object. With AutoCAD already open, this is
  skipped entirely (`start()` tries `GetActiveObject` first).

Then run the exact same server as before:

```bat
python -m apps.server.server
REM or
uvicorn apps.api.main:app --reload
```

The *only* difference is `cad.backend` in `config.json` — every tool,
every REST endpoint, every dashboard panel is identical code, now
driving real AutoCAD instead of the ezdxf writer.

---

## Part 4 — What to actually verify (checklist)

Go through each of these against the live AutoCAD session and note
anything that breaks, looks wrong, or throws a COM error:

- [ ] `create_layer` — new layer appears in AutoCAD's Layer Manager;
      calling it twice with the same name doesn't error or duplicate
- [ ] `draw_line`, `draw_circle`, `draw_arc`, `draw_ellipse` — each
      appears correctly positioned/sized; units match what was sent
- [ ] `draw_polyline` (open and closed), `draw_rectangle` — vertex
      order and closure correct
- [ ] `draw_text` — position, height, and rotation all correct
- [ ] `draw_hatch` — the one entity type never even simulated against
      ezdxf the same way; watch closely for `AddHatch`/
      `AppendOuterLoop`/`Evaluate()` COM errors
- [ ] `add_dimension` — aligned dimension line + text position land
      correctly
- [ ] `color`/`lineweight` fields actually apply per-entity
- [ ] `save_drawing` — saves into the configured output directory and
      the file reopens correctly afterward
- [ ] `insert_symbol` / `insert_title_block` — multi-entity inserts
      land as a coherent group, not scattered
- [ ] `process_command` (natural language) — same NLP parser, now
      executing against AutoCAD instead of DXF
- [ ] Batched `Regen()` — the viewport refreshes once per plan (not
      once per entity), and you don't have to manually zoom/regen to
      see new geometry appear
- [ ] A longer session, many tool calls in a row — no COM object leaks,
      no "RPC server unavailable" errors

Also worth deliberately probing: the `_known_layers` cache is seeded
once at `start()` and never re-synced — if you delete a layer from
inside AutoCAD's own UI (not through this tool) and then try to
recreate it via `create_layer`, the cache won't know it's gone. That's
a plausible real edge case, not yet fixed, worth confirming.

---

## Part 5 — Report back

Whatever you find — bugs, COM quirks, or everything working
perfectly — bring the specifics (error messages, screenshots, which
checklist item) back to a Claude Code session pointed at this repo.
Since `autocad/backend.py` has never touched a real AutoCAD install
until now, this is the first real signal on whether it needs fixes.
