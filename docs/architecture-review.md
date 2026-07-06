# Architecture Review — CAD-MCP (daobataotie/CAD-MCP)

Phase 1 deliverable. Base repository: https://github.com/daobataotie/CAD-MCP
(commit at time of review: default branch, ~2,900 lines of Python total).

## 1. What the repository actually is

CAD-MCP is a single-process **MCP stdio server** that lets an LLM client
(Claude Desktop, Cursor, MCP Inspector) drive AutoCAD/GstarCAD/ZWCAD through
Windows COM automation. It is a weekend-project-scale proof of concept, not
a platform: 4 source files, no tests, no CI, no packaging, no persistence
layer, no abstraction boundaries beyond "one class per concern."

```
src/
├── __init__.py        (53 lines)  — loads config.json, module-level singleton
├── server.py         (1129 lines) — MCP tool definitions + stdio transport
├── cad_controller.py  (720 lines) — win32com COM driver for AutoCAD-family apps
└── nlp_processor.py   (523 lines) — regex/keyword command parser (no LLM)
```

## 2. Existing architecture

```
MCP Client (Claude/Cursor)
        │  stdio (JSON-RPC over stdin/stdout)
        ▼
  server.py::main()
   ├─ Config              (reads src/config.json, no env override, no validation)
   ├─ CADService           — thin orchestration + in-memory "drawing_state" dict
   │    ├─ NLPProcessor    — regex parser: shape keywords → coordinates → params
   │    └─ CADController   — win32com.client.Dispatch("AutoCAD.Application")
   │                          synchronous COM calls: AddLine/AddCircle/AddArc/
   │                          AddEllipse/AddPolyline/AddHatch/AddDimAligned
   └─ MCP tool handlers    — one `if name == "draw_x"` branch per tool,
                             duplicated argument-validation/color-extraction
                             boilerplate in both `process_command` and
                             `handle_call_tool`
```

Execution flow: MCP tool call → argument dict → `CADService` method →
`CADController` method → COM call into a **live, GUI-visible AutoCAD
process** → entity handle returned → appended to an in-memory list that is
never persisted to disk. There is no notion of a "document," "project," or
"drawing plan" — every tool call is an immediate, irreversible mutation of
whatever CAD document happens to be active.

## 3. Modules and responsibilities (as they exist today)

| Module | Responsibility | Notes |
|---|---|---|
| `server.py` | MCP protocol surface, tool schemas, dispatch | Tool list and `handle_call_tool`/`process_command` duplicate the same 11 branches almost verbatim (~600 of 1129 lines are repetitive argument plumbing) |
| `cad_controller.py` | COM automation against AutoCAD/GCAD/ZWCAD | Direct, synchronous, blocking win32com calls; one flat class, no interface/backend abstraction despite "supporting 3 CAD apps" |
| `nlp_processor.py` | Natural-language → command dict | Pure regex + hardcoded Chinese/English keyword tables; extracts first N numbers/coordinates positionally — no grammar, no LLM, no unit handling, no disambiguation |
| `__init__.py` | Config bootstrap | Loads JSON at import time as a side effect; duplicated (worse) fallback config also embedded here, diverging from `config.json` |
| `config.json` | Static config | No env-var override, no per-deployment profiles, no secrets handling (not that any exist yet) |

There is no geometry engine, no validation, no export pipeline, no
persistence, no plugin system, no REST API, no dashboard, no test suite —
this is the entire codebase.

## 4. Missing components (relative to any "platform")

Everything above the COM driver line is missing:
- **Planning layer** — no intent detection, no plan/geometry-plan separation; NL text is parsed directly into a single tool call.
- **Geometry/constraint/dimension/layer/annotation/template/validation/rendering/revision/export engines** — none exist; every "feature" is one COM call.
- **CAD abstraction interface** — `cad_controller.py` claims to support AutoCAD/GCAD/ZWCAD via one `if/elif` on `app_id` string, but the object model calls (`AddLine`, `AddDimAligned`, etc.) are AutoCAD's ActiveX API assumed to be identical on all three; no adapter/interface layer, no capability negotiation, no way to add FreeCAD/Fusion 360/etc. without touching this file.
- **File format engines (DXF/DWG/SVG/PDF/LISP/SCR export)** — the only "export" is `Document.SaveAs`, which requires a live COM-attached CAD instance; there is no headless DXF writer, so nothing can run in CI or on Linux/macOS.
- **Symbol/template/knowledge libraries** — no ANSI/ISO/IEC/ASME standards data, no symbol blocks, no title blocks.
- **Multi-project/persistence layer** — `drawing_state` is an in-process Python list; it is lost on process restart and is never written to disk or a DB.
- **REST API / dashboard / plugin SDK / agents** — none exist; the only interface is the MCP stdio tool surface.
- **Tests, CI/CD, packaging** — no `tests/`, no GitHub Actions, no `pyproject.toml`/build metadata, no version pinning (`requirements.txt` pins nothing except lower bounds, and `typing>=3.7.4.3` is a defunct backport package that will fail to install on any Python ≥3.5, where `typing` is stdlib).

## 5. Technical debt

- **Massive duplication**: `process_command` (NLP path) and `handle_call_tool` (direct MCP path) reimplement the same 11 operations with near-identical argument extraction, color-resolution, and error-message logic. Any new drawing primitive requires editing 4 places (NLP parser, `CADService` method, `CADController` method, and both dispatch branches in `server.py`).
- **God-function `start_cad`**: nested `try/except` up to 4 levels deep, nested app-identifier `if/elif` chains repeated twice (lines ~54–70 and ~112–126) inside the same method, nested try inside `except` inside `try` — a change to the CAD-type list must be made in two places in one function.
- **Bug**: `start_cad`'s `finally` block references `old_app`, a name only bound inside the `try` block — if an exception occurs before that assignment, `finally` raises `NameError: name 'old_app' is not defined`, masking the real error.
- **Dead/inconsistent code**: `create_layer`'s `color` parameter is accepted but commented out in the body; `__init__.py` embeds a second, divergent hardcoded default config that will silently drift from `config.json`.
- **No type safety enforced**: type hints exist but nothing validates inputs (e.g., `radius` can be `None` and will NPE deep inside COM marshalling rather than failing a schema check, despite JSON-schema `inputSchema` being defined for MCP tools — pydantic is a declared dependency but never actually used for validation).
- **Global mutable state**: `config` is loaded at module import time in three different files independently (`__init__.py`, `cad_controller.py`, `nlp_processor.py`), each re-reading and re-parsing `config.json` from disk with its own relative-path resolution.

## 6. Scalability issues

- **Single synchronous COM session**: `CADController` holds exactly one `app`/`doc` reference; the design cannot run multiple drawings, multiple users, or multiple CAD sessions concurrently — it is fundamentally single-tenant, single-document.
- **Blocking calls inside `async def` handlers**: every `handle_call_tool` branch calls straight into blocking win32com COM calls with no `run_in_executor`/thread offload, so a slow COM call (the code itself waits `startup_wait_time=20s` synchronously) blocks the entire asyncio event loop and the MCP server can service no other request meanwhile.
- **No queuing/job model**: nothing supports "streaming generation," batching, or long-running drawing jobs; large drawings (the target platform's "100k+ entities" requirement) would be issued as thousands of sequential COM round-trips with a `Regen` view refresh after almost every single call (`refresh_view()` is invoked per-entity) — this is the opposite of the platform's performance goals.
- **No horizontal scaling story**: because it depends on `GetActiveObject`/`Dispatch` against a real, GUI-attached Windows CAD process, this cannot be containerized, load-balanced, or run headless — it can only ever run 1 instance per Windows desktop session with CAD installed.

## 7. Security issues

- **Unsanitized file paths**: `save_drawing(file_path)` and the NLP-derived `file_path` (`_parse_save`) pass user/LLM-supplied strings straight to `os.makedirs`/`Document.SaveAs` with no path normalization or containment check — a malicious or confused prompt can write/overwrite arbitrary paths on the host filesystem (path traversal, e.g. `../../Windows/System32/...`).
- **No auth/authorization boundary**: the MCP stdio transport has no concept of a caller identity or permission scope; anything that can talk to the process stdin can invoke any tool, including `save_drawing` to an arbitrary path.
- **No input validation despite declared schemas**: MCP `inputSchema` JSON Schemas are declared for documentation/tooling purposes but never enforced server-side (pydantic is imported but only used for `AnyUrl`), so malformed types can propagate into COM calls with unpredictable native-level failures.
- **Verbose error leakage**: raw exception strings (including internal file paths and COM error text) are returned directly to the MCP client in tool responses (`f"错误: {str(e)}"`), which is fine for a local dev tool but is an information-disclosure smell if this code path is ever exposed beyond a trusted local desktop client.
- **Logging to a fixed relative file** (`cad_mcp.log`) with no rotation/size cap — unbounded log growth on long-running processes.

## 8. Performance bottlenecks

- `time.sleep(self.startup_wait_time)` (default 20s) and a hardcoded extra `time.sleep(2)` block the event loop synchronously on every cold CAD start.
- `refresh_view()` calls `Document.Regen(1)` after **every single entity**, which is O(n) full-viewport regenerations for n entities — for the platform's stated "100k+ entities" goal this is not just slow, it is unusable (should be batched/deferred and regenerated once per logical operation or on demand).
- Per-call `create_layer` does a **linear scan** (`for i in range(self.doc.Layers.Count)`) over all existing layers on every single draw call that specifies a layer, rather than caching known layer names — O(n·m) for n entities across m layers.
- Every geometry call constructs `win32com.client.VARIANT` arrays and does one Python↔COM marshalling round trip per primitive; there is no batching API (e.g., `AddLines`/bulk insert or a transaction pattern) despite AutoCAD's COM API supporting batch operations in some cases.

## 9. Design decisions worth keeping

Not everything is wrong — a redesign should preserve:
- **MCP-first interface**: exposing CAD operations as MCP tools is the correct integration point for LLM clients; this should remain a supported surface, not be replaced.
- **Attach-to-running-instance-first, launch-as-fallback** CAD connection strategy — reasonable default UX for a desktop tool.
- **Structured tool schemas** (JSON Schema per tool) — the shape is right, it just needs to be *enforced*, not just declared.

## 10. Opportunities for redesign

1. **Introduce a `CADBackend` interface** (abstract base class / `Protocol`) with concrete adapters (`AutoCADBackend`, `ZWCADBackend`, `GstarCADBackend`, later `DXFBackend` for headless/CI use via `ezdxf`) instead of one class branching on a string.
2. **Separate planning from execution**: parse intent → build a structured, serializable "drawing plan" (geometry + validation results) → *then* execute against a backend. This also enables dry-run/validate-only workflows and undo/redo, which the current design cannot support at all (no plan artifact ever exists).
3. **Replace the regex NLP layer** with an LLM-backed planner that emits the same structured plan schema, with the regex parser demoted to a cheap fallback/offline mode.
4. **Make CAD execution headless-testable**: add a DXF-based backend (via `ezdxf`) so geometry/validation/planning logic can be unit-tested in CI without Windows or a CAD license — this is the single highest-leverage change for making the rest of the roadmap (tests, CI, plugin SDK) achievable at all.
5. **Fix the blocking-call/async mismatch** by running COM/backend calls in a thread executor, and batch view-regeneration instead of per-entity.
6. **Add a validation boundary**: enforce the already-declared JSON schemas server-side before any backend call, and sanitize/contain all file-path inputs to a configured output root.
7. **Consolidate the duplicated dispatch logic** in `server.py` into a single command-registry (name → schema → handler) used by both `process_command` and `handle_call_tool`, eliminating the ~600 duplicated lines.

## 11. Implication for the requested platform scope

The master vision (AI planning → reasoning → multi-CAD execution → validation
→ REST API → dashboard → plugin marketplace → standards knowledge base →
multi-format import/export → 100k-entity performance) is not an extension of
this codebase — it is a ground-up platform that happens to reuse the idea of
"COM-driven CAD backend behind an MCP surface" as one of several execution
targets. Realistically this is many weeks of focused engineering, not a
single session. The highest-leverage next step is building the pieces that
*unlock* everything else and can be verified without Windows/AutoCAD: the
planning pipeline, a `CADBackend` interface, a headless DXF backend, and a
validation engine — then layering CAD-specific execution, REST/dashboard,
and the symbol/knowledge libraries on top once that spine exists and is
tested.
