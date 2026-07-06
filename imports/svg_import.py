"""Import a constrained subset of SVG into a list of CAD entities.

Supported elements: `line`, `circle`, `ellipse`, `rect`, `polyline`,
`polygon`, `text`, and `path` (straight-segment commands `M`/`L`/`H`/`V`/`Z`
only, both absolute and relative). Anything else — curves (`C`/`S`/`Q`/`T`/
`A`), `<g>` transforms, CSS styling/color — is out of scope for this pass
and either produces a warning (unsupported element, entirely skipped) or is
silently ignored (structural elements like `<svg>`/`<defs>`/`<title>`).

SVG's y-axis points down; CAD's points up. Every imported coordinate is
flipped against the document height (from `viewBox` or the `height`
attribute, falling back to a plain sign flip with no reference height if
neither is present) so imported geometry reads right-side-up.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Tuple
from xml.etree import ElementTree as ET

from engine.geometry.primitives import (
    CircleEntity,
    EllipseEntity,
    Entity,
    LineEntity,
    PolylineEntity,
    RectangleEntity,
    TextEntity,
)

Point2 = Tuple[float, float]

MAX_SVG_BYTES = 2_000_000

_SUPPORTED_PATH_CMDS = set("MmLlHhVvZz")
_ALL_PATH_CMD_RE = re.compile(r"[MmLlHhVvZzCcSsQqTtAa]")
_PATH_TOKEN_RE = re.compile(r"[MmLlHhVvZz]|-?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][-+]?\d+)?")

_IGNORED_TAGS = {"svg", "g", "defs", "title", "desc", "metadata", "style"}


class SvgImportError(Exception):
    """Raised when the SVG can't be parsed at all (bad XML, no geometry)."""


def import_svg(svg_content: str) -> Tuple[List[Entity], List[str]]:
    """Parse `svg_content`. Returns (entities, warnings) or raises
    SvgImportError if the document is malformed or has no usable geometry."""
    if len(svg_content.encode("utf-8", errors="ignore")) > MAX_SVG_BYTES:
        raise SvgImportError(f"SVG exceeds the {MAX_SVG_BYTES} byte import limit")
    lowered = svg_content.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise SvgImportError("SVG must not contain a DOCTYPE or ENTITY declaration")

    try:
        root = ET.fromstring(svg_content)
    except ET.ParseError as exc:
        raise SvgImportError(f"invalid SVG/XML: {exc}") from exc

    height = _flip_height(root)
    entities: List[Entity] = []
    warnings: List[str] = []
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag in _IGNORED_TAGS:
            continue
        handler = _HANDLERS.get(tag)
        if handler is None:
            warnings.append(f"unsupported element <{tag}> skipped")
            continue
        try:
            entities.extend(handler(elem, height))
        except ValueError as exc:
            warnings.append(f"<{tag}> skipped: {exc}")

    if not entities:
        raise SvgImportError("no supported geometry found in SVG")
    return entities, warnings


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _flip_height(root: ET.Element) -> float:
    view_box = root.get("viewBox")
    if view_box:
        parts = view_box.replace(",", " ").split()
        if len(parts) == 4:
            try:
                return float(parts[3])
            except ValueError:
                pass
    height_attr = root.get("height")
    if height_attr:
        match = re.match(r"[-\d.]+", height_attr)
        if match:
            return float(match.group())
    return 0.0


def _layer(elem: ET.Element) -> str:
    return elem.get("id") or "0"


def _pt(x: float, y: float, height: float) -> Tuple[float, float, float]:
    return (x, height - y, 0.0)


def _float(elem: ET.Element, attr: str, default: float = 0.0) -> float:
    value = elem.get(attr)
    return float(value) if value is not None else default


def _parse_points(points_attr: str) -> List[Point2]:
    nums = [float(n) for n in re.split(r"[\s,]+", points_attr.strip()) if n]
    if len(nums) < 4 or len(nums) % 2 != 0:
        raise ValueError("points list needs an even number of coordinates, at least 2 points")
    return list(zip(nums[0::2], nums[1::2]))


def _handle_line(elem: ET.Element, height: float) -> List[Entity]:
    layer = _layer(elem)
    start = _pt(_float(elem, "x1"), _float(elem, "y1"), height)
    end = _pt(_float(elem, "x2"), _float(elem, "y2"), height)
    return [LineEntity(start=start, end=end, layer=layer)]


def _handle_circle(elem: ET.Element, height: float) -> List[Entity]:
    radius = _float(elem, "r")
    if radius <= 0:
        raise ValueError("radius must be positive")
    center = _pt(_float(elem, "cx"), _float(elem, "cy"), height)
    return [CircleEntity(center=center, radius=radius, layer=_layer(elem))]


def _handle_ellipse(elem: ET.Element, height: float) -> List[Entity]:
    rx, ry = _float(elem, "rx"), _float(elem, "ry")
    if rx <= 0 or ry <= 0:
        raise ValueError("rx/ry must be positive")
    center = _pt(_float(elem, "cx"), _float(elem, "cy"), height)
    major, minor, rotation = (rx, ry, 0.0) if rx >= ry else (ry, rx, 90.0)
    return [
        EllipseEntity(
            center=center, major_axis=major, minor_axis=minor, rotation=rotation, layer=_layer(elem)
        )
    ]


def _handle_rect(elem: ET.Element, height: float) -> List[Entity]:
    x, y = _float(elem, "x"), _float(elem, "y")
    width, rect_height = _float(elem, "width"), _float(elem, "height")
    if width <= 0 or rect_height <= 0:
        raise ValueError("width/height must be positive")
    corner1 = _pt(x, y, height)
    corner2 = _pt(x + width, y + rect_height, height)
    return [RectangleEntity(corner1=corner1, corner2=corner2, layer=_layer(elem))]


def _handle_polyline(elem: ET.Element, height: float, closed: bool = False) -> List[Entity]:
    points = _parse_points(elem.get("points", ""))
    points3 = [_pt(x, y, height) for x, y in points]
    return [PolylineEntity(points=points3, closed=closed, layer=_layer(elem))]


def _handle_polygon(elem: ET.Element, height: float) -> List[Entity]:
    return _handle_polyline(elem, height, closed=True)


def _handle_text(elem: ET.Element, height: float) -> List[Entity]:
    content = (elem.text or "").strip()
    if not content:
        raise ValueError("empty text content")
    position = _pt(_float(elem, "x"), _float(elem, "y"), height)
    text_height = 2.5
    font_size = elem.get("font-size")
    if font_size:
        match = re.match(r"[-\d.]+", font_size)
        if match:
            text_height = float(match.group())
    return [TextEntity(position=position, text=content, height=text_height, layer=_layer(elem))]


def _handle_path(elem: ET.Element, height: float) -> List[Entity]:
    d = elem.get("d", "")
    if not d:
        raise ValueError("empty path data")
    subpaths = _parse_path_data(d)
    layer = _layer(elem)
    entities: List[Entity] = []
    for points, closed in subpaths:
        if len(points) < 2:
            continue
        points3 = [_pt(x, y, height) for x, y in points]
        entities.append(PolylineEntity(points=points3, closed=closed, layer=layer))
    if not entities:
        raise ValueError("path produced no usable line segments")
    return entities


def _parse_path_data(d: str) -> List[Tuple[List[Point2], bool]]:
    used_cmds = set(_ALL_PATH_CMD_RE.findall(d))
    unsupported = used_cmds - _SUPPORTED_PATH_CMDS
    if unsupported:
        raise ValueError(f"unsupported path command(s) {sorted(unsupported)} (curves aren't supported)")

    tokens = _PATH_TOKEN_RE.findall(d)
    subpaths: List[Tuple[List[Point2], bool]] = []
    current: List[Point2] = []
    cmd = ""
    cur_x = cur_y = start_x = start_y = 0.0
    i = 0

    def read_num() -> float:
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    while i < len(tokens):
        tok = tokens[i]
        if len(tok) == 1 and tok in _SUPPORTED_PATH_CMDS:
            cmd = tok
            i += 1
            if cmd in "Zz":
                if current:
                    subpaths.append((current, True))
                current = []
                cur_x, cur_y = start_x, start_y
            continue
        if not cmd:
            raise ValueError("path data must start with a moveto command")
        if cmd in "Mm":
            x, y = read_num(), read_num()
            if cmd == "m":
                x, y = x + cur_x, y + cur_y
            if current:
                subpaths.append((current, False))
            current = [(x, y)]
            cur_x, cur_y, start_x, start_y = x, y, x, y
            cmd = "L" if cmd == "M" else "l"
        elif cmd in "Ll":
            x, y = read_num(), read_num()
            if cmd == "l":
                x, y = x + cur_x, y + cur_y
            current.append((x, y))
            cur_x, cur_y = x, y
        elif cmd in "Hh":
            x = read_num()
            if cmd == "h":
                x += cur_x
            current.append((x, cur_y))
            cur_x = x
        elif cmd in "Vv":
            y = read_num()
            if cmd == "v":
                y += cur_y
            current.append((cur_x, y))
            cur_y = y

    if current:
        subpaths.append((current, False))
    return subpaths


_HANDLERS: Dict[str, Callable[[ET.Element, float], List[Entity]]] = {
    "line": _handle_line,
    "circle": _handle_circle,
    "ellipse": _handle_ellipse,
    "rect": _handle_rect,
    "polyline": _handle_polyline,
    "polygon": _handle_polygon,
    "text": _handle_text,
    "path": _handle_path,
}
