"""AutoCADBackend: COM automation driver for AutoCAD, GstarCAD, and ZWCAD.

This refactors the original repo's `cad_controller.py`, fixing the issues
called out in `docs/architecture-review.md`:

- `old_app` was referenced in a `finally` block but only ever assigned
  inside the preceding `try`, so an early exception raised `NameError`
  and masked the real failure. Fixed by not keeping a stale-instance
  reference at all — `start()` simply (re)assigns `self.app`/`self.doc`.
- The AutoCAD/GCAD/ZWCAD app-id lookup was a 3-way `if/elif` chain
  duplicated verbatim in two different code paths. Replaced with one
  `APP_IDS` table used everywhere.
- `refresh_view()` (`Document.Regen`) was called after every single
  entity — O(n) full-viewport regenerations for n entities. `execute()`
  now regenerates once per plan.
- `create_layer` did a linear scan over every existing layer on every
  drawing call. Now backed by a name cache seeded once at `start()`.
- Lineweight validation is handled once, at the `Entity` schema level
  (see `engine.geometry.primitives`), instead of being re-implemented
  here and silently coercing invalid values to 0.
- `save()` routes through `cad.backend.resolve_safe_path` instead of
  passing a user/LLM-supplied path straight to `SaveAs`.

This module has not been executed against a real CAD application — there
is no Windows/AutoCAD environment available where this was written. It is
written to be correct by inspection and mirrors the original's proven COM
call sequence; verify it against a live AutoCAD/GstarCAD/ZWCAD install
before relying on it.
"""

from __future__ import annotations

import logging
import math
import os
import time
from functools import partial
from typing import Any

try:
    import pythoncom
    import win32com.client
except ImportError as exc:  # pragma: no cover - exercised only on Windows
    raise ImportError(
        "autocad.backend requires pywin32 and a Windows environment with "
        "AutoCAD, GstarCAD, or ZWCAD installed. Use the 'dxf' backend for "
        "headless or non-Windows use."
    ) from exc

from cad.backend import CADBackend, EntityResult, resolve_safe_path
from cad.registry import register_backend
from engine.geometry.engine import GeometryEngine
from engine.geometry.primitives import Entity, Point3

logger = logging.getLogger("autocad_backend")

# app_type -> (COM ProgID, display name). GstarCAD and "gcad" share a ProgID;
# both names are registered as aliases in cad.registry.
APP_IDS = {
    "autocad": ("AutoCAD.Application", "AutoCAD"),
    "gcad": ("GCAD.Application", "GstarCAD"),
    "zwcad": ("ZWCAD.Application", "ZWCAD"),
}


def _variant_point(point: Point3):
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, list(point))


def _variant_doubles(values):
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, list(values))


class AutoCADBackend(CADBackend):
    name = "autocad"

    def __init__(
        self,
        app_type: str = "autocad",
        startup_wait_time: float = 20.0,
        output_dir: str = "./output",
        **_ignored: Any,
    ) -> None:
        key = app_type.lower()
        if key not in APP_IDS:
            raise ValueError(f"unknown AutoCAD-family app_type {app_type!r}; expected one of {list(APP_IDS)}")
        self._app_id, self._app_name = APP_IDS[key]
        self.startup_wait_time = startup_wait_time
        self.output_dir = output_dir
        self.app = None
        self.doc = None
        self._known_layers: set = set()
        self._geometry = GeometryEngine()

    def start(self) -> bool:
        pythoncom.CoInitialize()
        try:
            self.app = win32com.client.GetActiveObject(self._app_id)
            logger.info("Connected to running %s instance", self._app_name)
        except Exception:
            logger.info("No running %s instance found; launching a new one", self._app_name)
            self.app = win32com.client.Dispatch(self._app_id)
            self.app.Visible = True
            time.sleep(self.startup_wait_time)

        self.doc = self.app.Documents.Add() if self.app.Documents.Count == 0 else self.app.ActiveDocument
        if self.doc is None:
            raise RuntimeError(f"failed to obtain a valid {self._app_name} document")
        _ = self.doc.Name  # sanity check: raises if the document handle is invalid

        self._known_layers = {self.doc.Layers.Item(i).Name for i in range(self.doc.Layers.Count)}
        logger.info("%s ready (document: %s)", self._app_name, self.doc.Name)
        return True

    def is_running(self) -> bool:
        return self.app is not None and self.doc is not None

    def create_layer(self, layer_name: str) -> bool:
        if layer_name in self._known_layers:
            return True
        self.doc.Layers.Add(layer_name)
        self._known_layers.add(layer_name)
        return True

    def execute(self, plan):
        result = super().execute(plan)
        self.refresh_view()
        return result

    def refresh_view(self) -> None:
        if not self.is_running():
            return
        try:
            self.doc.Regen(1)  # acAllViewports
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to refresh view: %s", exc)

    def draw_entity(self, entity: Entity) -> EntityResult:
        handler = getattr(self, f"_draw_{entity.type}", None)
        if handler is None:
            error = f"unsupported entity type '{entity.type}'"
            return EntityResult(entity_type=entity.type, success=False, error=error)
        try:
            obj = handler(entity)
            self._apply_common_attribs(obj, entity)
            return EntityResult(entity_type=entity.type, success=True, handle=obj.Handle)
        except Exception as exc:  # noqa: BLE001 - COM errors surface as generic exceptions
            return EntityResult(entity_type=entity.type, success=False, error=str(exc))

    def _apply_common_attribs(self, obj, entity: Entity) -> None:
        if entity.layer:
            obj.Layer = entity.layer
        if entity.color is not None:
            obj.Color = entity.color
        if entity.lineweight is not None:
            obj.LineWeight = entity.lineweight

    def _draw_line(self, e):
        return self.doc.ModelSpace.AddLine(_variant_point(e.start), _variant_point(e.end))

    def _draw_circle(self, e):
        return self.doc.ModelSpace.AddCircle(_variant_point(e.center), e.radius)

    def _draw_arc(self, e):
        return self.doc.ModelSpace.AddArc(
            _variant_point(e.center), e.radius, math.radians(e.start_angle), math.radians(e.end_angle)
        )

    def _draw_ellipse(self, e):
        rotation_rad = math.radians(e.rotation)
        major_vector = _variant_point(
            (e.major_axis * math.cos(rotation_rad), e.major_axis * math.sin(rotation_rad), 0.0)
        )
        ratio = e.minor_axis / e.major_axis
        return self.doc.ModelSpace.AddEllipse(_variant_point(e.center), major_vector, ratio)

    def _polyline_points(self, points, closed: bool):
        flat = [coord for point in points for coord in point]
        polyline = self.doc.ModelSpace.AddPolyline(_variant_doubles(flat))
        if closed and len(points) > 2:
            polyline.Closed = True
        return polyline

    def _draw_polyline(self, e):
        return self._polyline_points(e.points, e.closed)

    def _draw_rectangle(self, e):
        return self._polyline_points(self._geometry.rectangle_corners(e), closed=True)

    def _draw_text(self, e):
        text_obj = self.doc.ModelSpace.AddText(e.text, _variant_point(e.position), e.height)
        if e.rotation:
            text_obj.Rotation = math.radians(e.rotation)
        return text_obj

    def _draw_hatch(self, e):
        boundary = self._polyline_points(e.points, closed=True)
        hatch = self.doc.ModelSpace.AddHatch(0, e.pattern_name, True)
        boundary_ids = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [boundary])
        hatch.AppendOuterLoop(boundary_ids)
        hatch.PatternScale = e.scale
        hatch.Evaluate()
        return hatch

    def _draw_dimension(self, e):
        if e.text_position is not None:
            text_position = e.text_position
        else:
            text_position = ((e.start[0] + e.end[0]) / 2, (e.start[1] + e.end[1]) / 2 + 5, 0.0)
        dimension = self.doc.ModelSpace.AddDimAligned(
            _variant_point(e.start), _variant_point(e.end), _variant_point(text_position)
        )
        dimension.TextHeight = e.textheight
        return dimension

    def save(self, file_path: str) -> bool:
        if not self.is_running():
            return False
        safe_path = resolve_safe_path(self.output_dir, file_path)
        os.makedirs(os.path.dirname(safe_path) or ".", exist_ok=True)
        self.doc.SaveAs(safe_path)
        logger.info("Saved drawing to %s", safe_path)
        return True


register_backend("autocad", partial(AutoCADBackend, app_type="autocad"))
register_backend("gcad", partial(AutoCADBackend, app_type="gcad"))
register_backend("gstarcad", partial(AutoCADBackend, app_type="gcad"))
register_backend("zwcad", partial(AutoCADBackend, app_type="zwcad"))
