"""Derived-geometry computation shared by the validator and CAD backends."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from engine.geometry.primitives import (
    ArcEntity,
    CircleEntity,
    DimensionEntity,
    EllipseEntity,
    Entity,
    HatchEntity,
    LineEntity,
    Point3,
    PolylineEntity,
    RectangleEntity,
    TextEntity,
)


@dataclass(frozen=True)
class BoundingBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def overlaps(self, other: "BoundingBox") -> bool:
        return not (
            self.max_x < other.min_x
            or other.max_x < self.min_x
            or self.max_y < other.min_y
            or other.max_y < self.min_y
        )

    @classmethod
    def from_points(cls, points: List[Point3]) -> "BoundingBox":
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return cls(min(xs), min(ys), max(xs), max(ys))


class GeometryEngine:
    """Computes geometry derived from the primitive entity models.

    Kept separate from `primitives.py` because these are computations
    (with judgment calls about approximation, e.g. arc/ellipse bounding
    boxes), not data definitions.
    """

    def rectangle_corners(self, entity: RectangleEntity) -> List[Point3]:
        x1, y1, z1 = entity.corner1
        x2, y2, _ = entity.corner2
        return [
            (x1, y1, z1),
            (x2, y1, z1),
            (x2, y2, z1),
            (x1, y2, z1),
            (x1, y1, z1),
        ]

    def bounding_box(self, entity: Entity) -> BoundingBox:
        if isinstance(entity, LineEntity):
            return BoundingBox.from_points([entity.start, entity.end])
        if isinstance(entity, CircleEntity):
            cx, cy, _ = entity.center
            r = entity.radius
            return BoundingBox(cx - r, cy - r, cx + r, cy + r)
        if isinstance(entity, ArcEntity):
            return self._arc_bounding_box(entity)
        if isinstance(entity, EllipseEntity):
            return self._ellipse_bounding_box(entity)
        if isinstance(entity, PolylineEntity):
            return BoundingBox.from_points(entity.points)
        if isinstance(entity, RectangleEntity):
            return BoundingBox.from_points(self.rectangle_corners(entity))
        if isinstance(entity, TextEntity):
            # Exact glyph metrics aren't available here; the insertion point
            # is a conservative (zero-area) stand-in used only for coarse
            # overlap checks, not for rendering.
            return BoundingBox.from_points([entity.position])
        if isinstance(entity, HatchEntity):
            return BoundingBox.from_points(entity.points)
        if isinstance(entity, DimensionEntity):
            points = [entity.start, entity.end]
            if entity.text_position is not None:
                points.append(entity.text_position)
            return BoundingBox.from_points(points)
        raise TypeError(f"no bounding box computation for entity type {entity.type!r}")

    def _arc_bounding_box(self, entity: ArcEntity) -> BoundingBox:
        cx, cy, _ = entity.center
        r = entity.radius
        angles = [entity.start_angle, entity.end_angle]
        # Include any cardinal angle (where x/y extrema occur on a full
        # circle) that actually falls within the arc's sweep.
        start, end = entity.start_angle % 360, entity.end_angle % 360
        sweep = (end - start) % 360 or 360
        for cardinal in (0, 90, 180, 270):
            if (cardinal - start) % 360 <= sweep:
                angles.append(cardinal)
        points = [
            (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)), 0.0)
            for a in angles
        ]
        return BoundingBox.from_points(points)

    def _ellipse_bounding_box(self, entity: EllipseEntity) -> BoundingBox:
        cx, cy, _ = entity.center
        rot = math.radians(entity.rotation)
        # Axis-aligned bounding box of a rotated ellipse: project the major
        # and minor axis vectors onto x/y and combine in quadrature.
        ux, uy = entity.major_axis * math.cos(rot), entity.major_axis * math.sin(rot)
        vx, vy = -entity.minor_axis * math.sin(rot), entity.minor_axis * math.cos(rot)
        half_width = math.hypot(ux, vx)
        half_height = math.hypot(uy, vy)
        return BoundingBox(cx - half_width, cy - half_height, cx + half_width, cy + half_height)

    def plan_bounding_box(self, entities: List[Entity]) -> Optional[BoundingBox]:
        boxes = [self.bounding_box(e) for e in entities]
        if not boxes:
            return None
        return BoundingBox(
            min(b.min_x for b in boxes),
            min(b.min_y for b in boxes),
            max(b.max_x for b in boxes),
            max(b.max_y for b in boxes),
        )
