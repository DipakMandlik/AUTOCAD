"""Generate AutoCAD Script (.scr) and AutoLISP (.lsp) command sequences.

Neither format touches a live AutoCAD instance — they're text generation,
testable headlessly like everything else in `export/`. That also makes
this the one path to real AutoCAD that doesn't need `autocad.backend`
(Windows + pywin32 + a licensed install): run the .scr via AutoCAD's
SCRIPT command, or load the .lsp via APPLOAD, on any machine that has
AutoCAD — no COM automation required.

Both formats share one underlying model: for each entity, a "command
block" — the sequence of command-line inputs (command name, then each
prompted value in order, `""` for a bare Enter) exactly as if typed
interactively. `render_scr` joins those literally (one input per line);
`render_lisp` wraps each block in a single `(command ...)` form.

**Unverified.** There is no AutoCAD available in the environment this was
written in. The command sequences below follow documented AutoCAD
command-line prompt order; they have not been run against a real install.
Verify before relying on them, the same caveat as `autocad.backend`.

**Hatch is deliberately unsupported.** Unlike the COM backend's
`AddHatch` (a direct object-model call), scripting `-HATCH` means
replaying a multi-step interactive prompt sequence that has changed
across AutoCAD versions and depends on current dialog/settings state.
Guessing at that sequence with no way to verify it risks shipping a
plausible-looking script that silently fails or does the wrong thing —
worse than clearly not supporting it. `unsupported_entities()` reports
which plan entries were skipped so callers can surface that to the user.
"""

from __future__ import annotations

import math
from typing import Any, List, Sequence, Union

from engine.geometry.primitives import DrawingPlan, Entity, Point3

UNSUPPORTED_ENTITY_TYPES = {"hatch"}

Token = Union[str, float, int, Point3, Sequence[float]]
CommandBlock = List[Token]


def _fmt(n: float) -> str:
    text = f"{float(n):.6f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def _midpoint_offset(start: Point3, end: Point3, offset: float = 5.0) -> Point3:
    return ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2 + offset, 0.0)


def _entity_commands(entity: Entity) -> List[CommandBlock]:
    if entity.type == "line":
        return [["LINE", entity.start, entity.end, ""]]

    if entity.type == "circle":
        return [["CIRCLE", entity.center, entity.radius]]

    if entity.type == "arc":
        start_rad = math.radians(entity.start_angle)
        start_point = (
            entity.center[0] + entity.radius * math.cos(start_rad),
            entity.center[1] + entity.radius * math.sin(start_rad),
            0.0,
        )
        sweep = (entity.end_angle - entity.start_angle) % 360 or 360
        return [["ARC", "C", entity.center, start_point, "A", sweep]]

    if entity.type == "ellipse":
        rotation_rad = math.radians(entity.rotation)
        axis_endpoint = (
            entity.center[0] + entity.major_axis * math.cos(rotation_rad),
            entity.center[1] + entity.major_axis * math.sin(rotation_rad),
            0.0,
        )
        return [["ELLIPSE", "C", entity.center, axis_endpoint, entity.minor_axis]]

    if entity.type == "polyline":
        block: CommandBlock = ["PLINE", *entity.points]
        block.append("C" if entity.closed else "")
        return [block]

    if entity.type == "rectangle":
        return [["RECTANG", entity.corner1, entity.corner2]]

    if entity.type == "text":
        # A trailing blank closes TEXT's multi-line input loop after our
        # single line of content.
        return [["TEXT", entity.position, entity.height, entity.rotation, entity.text, ""]]

    if entity.type == "dimension":
        text_position = entity.text_position or _midpoint_offset(entity.start, entity.end)
        return [["DIMALIGNED", entity.start, entity.end, text_position]]

    if entity.type in UNSUPPORTED_ENTITY_TYPES:
        return []

    raise ValueError(f"no script command mapping for entity type {entity.type!r}")


def unsupported_entities(plan: DrawingPlan) -> List[int]:
    """Indices of plan operations that render_scr/render_lisp will skip."""
    return [i for i, e in enumerate(plan.operations) if e.type in UNSUPPORTED_ENTITY_TYPES]


def _all_command_blocks(plan: DrawingPlan) -> List[CommandBlock]:
    blocks: List[CommandBlock] = []
    for entity in plan.operations:
        blocks.extend(_entity_commands(entity))
    return blocks


def _scr_token(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (tuple, list)):
        return ",".join(_fmt(v) for v in value[:2])  # AutoCAD's 2D prompts ignore z
    return _fmt(value)


def render_scr(plan: DrawingPlan) -> str:
    lines: List[str] = []
    for block in _all_command_blocks(plan):
        lines.extend(_scr_token(token) for token in block)
    return "\n".join(lines) + "\n"


def _lisp_token(value: Any) -> str:
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (tuple, list)):
        return "'(" + " ".join(_fmt(v) for v in value[:2]) + ")"
    return _fmt(value)


def render_lisp(plan: DrawingPlan) -> str:
    lines: List[str] = []
    for block in _all_command_blocks(plan):
        args = " ".join(_lisp_token(token) for token in block)
        lines.append(f"(command {args})")
    return "\n".join(lines) + "\n"
