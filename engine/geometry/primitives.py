"""Typed geometry entities and the DrawingPlan container.

This module is the single shared vocabulary for the whole platform: the
planner produces these models, the validator inspects them, and every
CADBackend consumes them. No module here imports anything CAD- or
NLP-specific, so it stays usable from any context (including a future REST
API or dashboard) without dragging in win32com or MCP.
"""

from __future__ import annotations

from typing import Annotated, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, BeforeValidator, Field

# Valid AutoCAD lineweight values (in 1/100 mm), 0 = "by layer" default is not
# included on purpose: entities without an explicit lineweight simply omit it.
VALID_LINEWEIGHTS: Tuple[int, ...] = (
    0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53,
    60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211,
)


def _coerce_point(value: object) -> Tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) in (2, 3):
        x, y, *z = value
        return (float(x), float(y), float(z[0]) if z else 0.0)
    raise ValueError("point must be a 2 or 3 element [x, y, (z)] sequence")


Point3 = Annotated[Tuple[float, float, float], BeforeValidator(_coerce_point)]


class EntityBase(BaseModel):
    """Fields shared by every drawable entity."""

    layer: str = "0"
    color: Optional[int] = Field(default=None, ge=1, le=255)
    lineweight: Optional[int] = None

    def _validate_lineweight(self) -> None:
        if self.lineweight is not None and self.lineweight not in VALID_LINEWEIGHTS:
            raise ValueError(
                f"lineweight {self.lineweight} is not one of {VALID_LINEWEIGHTS}"
            )

    def model_post_init(self, __context: object) -> None:
        self._validate_lineweight()


class LineEntity(EntityBase):
    type: Literal["line"] = "line"
    start: Point3
    end: Point3


class CircleEntity(EntityBase):
    type: Literal["circle"] = "circle"
    center: Point3
    radius: float = Field(gt=0)


class ArcEntity(EntityBase):
    type: Literal["arc"] = "arc"
    center: Point3
    radius: float = Field(gt=0)
    start_angle: float
    end_angle: float


class EllipseEntity(EntityBase):
    type: Literal["ellipse"] = "ellipse"
    center: Point3
    major_axis: float = Field(gt=0)
    minor_axis: float = Field(gt=0)
    rotation: float = 0.0


class PolylineEntity(EntityBase):
    type: Literal["polyline"] = "polyline"
    points: List[Point3] = Field(min_length=2)
    closed: bool = False


class RectangleEntity(EntityBase):
    type: Literal["rectangle"] = "rectangle"
    corner1: Point3
    corner2: Point3


class TextEntity(EntityBase):
    type: Literal["text"] = "text"
    position: Point3
    text: str = Field(min_length=1)
    height: float = Field(default=2.5, gt=0)
    rotation: float = 0.0


class HatchEntity(EntityBase):
    type: Literal["hatch"] = "hatch"
    points: List[Point3] = Field(min_length=3)
    pattern_name: str = "SOLID"
    scale: float = Field(default=1.0, gt=0)


class DimensionEntity(EntityBase):
    type: Literal["dimension"] = "dimension"
    start: Point3
    end: Point3
    text_position: Optional[Point3] = None
    textheight: float = Field(default=5.0, gt=0)


Entity = Annotated[
    Union[
        LineEntity,
        CircleEntity,
        ArcEntity,
        EllipseEntity,
        PolylineEntity,
        RectangleEntity,
        TextEntity,
        HatchEntity,
        DimensionEntity,
    ],
    Field(discriminator="type"),
]

ENTITY_TYPES = {
    "line": LineEntity,
    "circle": CircleEntity,
    "arc": ArcEntity,
    "ellipse": EllipseEntity,
    "polyline": PolylineEntity,
    "rectangle": RectangleEntity,
    "text": TextEntity,
    "hatch": HatchEntity,
    "dimension": DimensionEntity,
}


class DrawingPlan(BaseModel):
    """A serializable, backend-agnostic description of what to draw.

    This is the artifact the whole pipeline is built around: planners
    produce it, the validator inspects and can rewrite it, and backends
    only ever consume it — no module downstream of planning talks to a
    CAD backend without going through a DrawingPlan first.
    """

    name: str = "untitled"
    units: str = "mm"
    source_text: Optional[str] = None
    operations: List[Entity] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
