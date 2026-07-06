"""DXFBackend: headless CADBackend implementation using ezdxf.

Unlike the COM backends, this never touches a live CAD process — it builds
an in-memory ezdxf document and writes a real .dxf file to disk. That makes
it the only backend that can run in CI, in a container, or on a developer
machine without a CAD license, which is why it is the platform's default.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict

import ezdxf

from cad.backend import CADBackend, EntityResult, resolve_safe_path
from cad.registry import register_backend
from engine.geometry.engine import GeometryEngine
from engine.geometry.primitives import Entity, Point3

logger = logging.getLogger("dxf_backend")


def _midpoint_offset(start: Point3, end: Point3, offset: float = 5.0) -> Point3:
    return ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2 + offset, 0.0)


class DXFBackend(CADBackend):
    name = "dxf"

    def __init__(self, output_dir: str = "./output", dxfversion: str = "R2010", **_ignored: Any) -> None:
        self.output_dir = output_dir
        self.doc = ezdxf.new(dxfversion=dxfversion)
        self.msp = self.doc.modelspace()
        self._known_layers = {"0"}
        self._geometry = GeometryEngine()
        logger.info("DXF backend initialized (dxfversion=%s)", dxfversion)

    def start(self) -> bool:
        return True  # the in-memory document is always ready

    def is_running(self) -> bool:
        return self.doc is not None

    def create_layer(self, layer_name: str) -> bool:
        if layer_name in self._known_layers:
            return True
        if layer_name not in self.doc.layers:
            self.doc.layers.add(layer_name)
        self._known_layers.add(layer_name)
        return True

    def draw_entity(self, entity: Entity) -> EntityResult:
        handler = getattr(self, f"_draw_{entity.type}", None)
        if handler is None:
            error = f"unsupported entity type '{entity.type}'"
            return EntityResult(entity_type=entity.type, success=False, error=error)

        attribs: Dict[str, Any] = {"layer": entity.layer}
        if entity.color is not None:
            attribs["color"] = entity.color
        if entity.lineweight is not None:
            attribs["lineweight"] = entity.lineweight

        try:
            obj = handler(entity, attribs)
            return EntityResult(entity_type=entity.type, success=True, handle=obj.dxf.handle)
        except Exception as exc:  # noqa: BLE001 - surface any ezdxf failure as a per-entity result
            return EntityResult(entity_type=entity.type, success=False, error=str(exc))

    def _draw_line(self, e, attribs):
        return self.msp.add_line(e.start, e.end, dxfattribs=attribs)

    def _draw_circle(self, e, attribs):
        return self.msp.add_circle(e.center, e.radius, dxfattribs=attribs)

    def _draw_arc(self, e, attribs):
        return self.msp.add_arc(e.center, e.radius, e.start_angle, e.end_angle, dxfattribs=attribs)

    def _draw_ellipse(self, e, attribs):
        rotation_rad = math.radians(e.rotation)
        major_axis_vector = (
            e.major_axis * math.cos(rotation_rad),
            e.major_axis * math.sin(rotation_rad),
            0.0,
        )
        ratio = e.minor_axis / e.major_axis
        return self.msp.add_ellipse(e.center, major_axis_vector, ratio, dxfattribs=attribs)

    def _draw_polyline(self, e, attribs):
        return self.msp.add_polyline3d(e.points, close=e.closed, dxfattribs=attribs)

    def _draw_rectangle(self, e, attribs):
        points = self._geometry.rectangle_corners(e)
        return self.msp.add_polyline3d(points, close=True, dxfattribs=attribs)

    def _draw_text(self, e, attribs):
        text_attribs = dict(attribs)
        text_attribs["rotation"] = e.rotation
        text = self.msp.add_text(e.text, height=e.height, dxfattribs=text_attribs)
        text.set_placement(e.position)
        return text

    def _draw_hatch(self, e, attribs):
        hatch = self.msp.add_hatch(color=attribs.get("color", 7), dxfattribs={"layer": attribs["layer"]})
        hatch.paths.add_polyline_path(e.points, is_closed=True)
        if e.pattern_name.upper() == "SOLID":
            hatch.set_solid_fill()
        else:
            hatch.set_pattern_fill(e.pattern_name, scale=e.scale)
        return hatch

    def _draw_dimension(self, e, attribs):
        text_position = e.text_position or _midpoint_offset(e.start, e.end)
        dim = self.msp.add_aligned_dim(p1=e.start, p2=e.end, distance=2, dxfattribs=attribs)
        dim.set_location(text_position)
        dim.render()
        return dim.dimension

    def save(self, file_path: str) -> bool:
        safe_path = resolve_safe_path(self.output_dir, file_path)
        os.makedirs(os.path.dirname(safe_path) or ".", exist_ok=True)
        self.doc.saveas(safe_path)
        logger.info("Saved DXF drawing to %s", safe_path)
        return True


register_backend("dxf", DXFBackend)
