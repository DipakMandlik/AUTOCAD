"""Symbol generator functions and the catalog that indexes them.

Every symbol is authored in a canonical local coordinate space (roughly
[0, 1] or [-1, 1], whatever reads clearly) and placed via `_transform`:
scale first (around the local origin), then rotate, then translate to the
requested world position. Arc entities need their start/end angles offset
by the rotation directly, since `ArcEntity` stores absolute angles rather
than an angle relative to some local frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from engine.geometry.primitives import (
    ArcEntity,
    CircleEntity,
    Entity,
    LineEntity,
    Point3,
    PolylineEntity,
    TextEntity,
)

Point2 = Tuple[float, float]
SymbolBuilder = Callable[..., List[Entity]]


def _transform(points: List[Point2], origin: Point3, scale: float, rotation_deg: float) -> List[Point3]:
    rad = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    ox, oy = origin[0], origin[1]
    oz = origin[2] if len(origin) > 2 else 0.0
    result = []
    for x, y in points:
        sx, sy = x * scale, y * scale
        rx = sx * cos_r - sy * sin_r
        ry = sx * sin_r + sy * cos_r
        result.append((ox + rx, oy + ry, oz))
    return result


def _line(points: List[Point2], origin: Point3, scale: float, rotation: float) -> LineEntity:
    start, end = _transform(points, origin, scale, rotation)
    return LineEntity(start=start, end=end)


def _polyline(
    points: List[Point2], origin: Point3, scale: float, rotation: float, closed: bool = False
) -> PolylineEntity:
    return PolylineEntity(points=_transform(points, origin, scale, rotation), closed=closed)


def _circle(center: Point2, radius: float, origin: Point3, scale: float, rotation: float) -> CircleEntity:
    (c,) = _transform([center], origin, scale, rotation)
    return CircleEntity(center=c, radius=radius * scale)


def _arc(
    center: Point2,
    radius: float,
    start_angle: float,
    end_angle: float,
    origin: Point3,
    scale: float,
    rotation: float,
) -> ArcEntity:
    (c,) = _transform([center], origin, scale, rotation)
    return ArcEntity(
        center=c, radius=radius * scale, start_angle=start_angle + rotation, end_angle=end_angle + rotation
    )


def _text(
    position: Point2, text: str, origin: Point3, scale: float, rotation: float, height: float = 0.3
) -> TextEntity:
    (p,) = _transform([position], origin, scale, rotation)
    return TextEntity(position=p, text=text, height=height * scale, rotation=rotation)


# --- Electrical --------------------------------------------------------


def resistor(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    zigzag = [(0, 0), (0.2, 0), (0.3, 0.5), (0.5, -0.5), (0.7, 0.5), (0.9, -0.5), (1.0, 0), (1.2, 0)]
    return [_polyline(zigzag, origin, scale, rotation)]


def capacitor(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _line([(0, 0), (0.45, 0)], origin, scale, rotation),
        _line([(0.45, -0.3), (0.45, 0.3)], origin, scale, rotation),
        _line([(0.55, -0.3), (0.55, 0.3)], origin, scale, rotation),
        _line([(0.55, 0), (1, 0)], origin, scale, rotation),
    ]


def ground(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _line([(0.5, 1), (0.5, 0.6)], origin, scale, rotation),
        _line([(0.2, 0.6), (0.8, 0.6)], origin, scale, rotation),
        _line([(0.3, 0.4), (0.7, 0.4)], origin, scale, rotation),
        _line([(0.4, 0.2), (0.6, 0.2)], origin, scale, rotation),
    ]


def battery_cell(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _line([(0, 0), (0.4, 0)], origin, scale, rotation),
        _line([(0.4, -0.4), (0.4, 0.4)], origin, scale, rotation),
        _line([(0.6, -0.2), (0.6, 0.2)], origin, scale, rotation),
        _line([(0.6, 0), (1, 0)], origin, scale, rotation),
    ]


# --- Piping / P&ID -------------------------------------------------------


def gate_valve(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _polyline([(0, 0.5), (0.5, 0), (0, -0.5)], origin, scale, rotation, closed=True),
        _polyline([(1, 0.5), (0.5, 0), (1, -0.5)], origin, scale, rotation, closed=True),
    ]


def pump(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _circle((0.5, 0), 0.5, origin, scale, rotation),
        _polyline([(0.3, 0.2), (0.3, -0.2), (0.7, 0)], origin, scale, rotation, closed=True),
    ]


# --- Architectural ---------------------------------------------------------


def door_swing(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _line([(0, 0), (0, 1)], origin, scale, rotation),
        _arc((0, 0), 1.0, 0.0, 90.0, origin, scale, rotation),
    ]


def window(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _polyline([(0, 0), (1, 0), (1, 0.2), (0, 0.2)], origin, scale, rotation, closed=True),
        _line([(0, 0.1), (1, 0.1)], origin, scale, rotation),
    ]


def north_arrow(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _line([(0, 0), (0, 1)], origin, scale, rotation),
        _polyline([(-0.15, 0.8), (0, 1), (0.15, 0.8)], origin, scale, rotation),
    ]


# --- Mechanical ----------------------------------------------------------


def bearing(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _circle((0.5, 0), 0.5, origin, scale, rotation),
        _circle((0.5, 0), 0.25, origin, scale, rotation),
    ]


def weld_symbol(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _line([(0, 0), (1, 0)], origin, scale, rotation),
        _polyline([(0.3, 0), (0.5, -0.3), (0.7, 0)], origin, scale, rotation, closed=True),
    ]


# --- HVAC ------------------------------------------------------------------


def diffuser(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _polyline([(0, 0), (1, 0), (1, 1), (0, 1)], origin, scale, rotation, closed=True),
        _line([(0, 0), (1, 1)], origin, scale, rotation),
        _line([(0, 1), (1, 0)], origin, scale, rotation),
    ]


def thermostat(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _circle((0.5, 0.5), 0.5, origin, scale, rotation),
        _text((0.38, 0.35), "T", origin, scale, rotation, height=0.35),
    ]


# --- Structural --------------------------------------------------------


def column(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    inner = [(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)]
    return [
        _polyline([(0, 0), (1, 0), (1, 1), (0, 1)], origin, scale, rotation, closed=True),
        _polyline(inner, origin, scale, rotation, closed=True),
    ]


def beam(origin: Point3, scale: float = 1.0, rotation: float = 0.0) -> List[Entity]:
    return [
        _line([(0, 1), (1, 1)], origin, scale, rotation),
        _line([(0, 0), (1, 0)], origin, scale, rotation),
        _line([(0.5, 0), (0.5, 1)], origin, scale, rotation),
    ]


@dataclass(frozen=True)
class SymbolDefinition:
    name: str
    discipline: str
    description: str
    build: SymbolBuilder


SYMBOL_LIBRARY: Dict[str, SymbolDefinition] = {
    definition.name: definition
    for definition in [
        SymbolDefinition("resistor", "electrical", "Zigzag resistor symbol", resistor),
        SymbolDefinition("capacitor", "electrical", "Two-plate capacitor symbol", capacitor),
        SymbolDefinition("ground", "electrical", "Earth/ground symbol", ground),
        SymbolDefinition("battery_cell", "electrical", "Single battery cell symbol", battery_cell),
        SymbolDefinition("gate_valve", "piping", "Gate valve (bowtie) symbol", gate_valve),
        SymbolDefinition("pump", "piping", "Centrifugal pump symbol", pump),
        SymbolDefinition("door_swing", "architectural", "Door leaf with 90-degree swing arc", door_swing),
        SymbolDefinition("window", "architectural", "Simple window symbol", window),
        SymbolDefinition("north_arrow", "architectural", "North arrow for site/floor plans", north_arrow),
        SymbolDefinition("bearing", "mechanical", "Concentric-circle bearing symbol", bearing),
        SymbolDefinition(
            "weld_symbol", "mechanical", "Reference line with a fillet-weld triangle", weld_symbol
        ),
        SymbolDefinition("diffuser", "hvac", "Square ceiling diffuser symbol (X pattern)", diffuser),
        SymbolDefinition(
            "thermostat", "hvac", "Circular thermostat symbol with a 'T' label", thermostat
        ),
        SymbolDefinition("column", "structural", "Column cross-section (nested squares)", column),
        SymbolDefinition("beam", "structural", "I-beam cross-section symbol", beam),
    ]
}
