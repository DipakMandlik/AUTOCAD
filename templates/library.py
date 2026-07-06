"""Parametrized drawing-sheet templates: a border plus a title block.

Sheet dimensions here are the public-domain ISO 216 / ANSI paper sizes
themselves (plain numbers, not licensed content) — this is not the same
thing as the licensed title-block *standards* (exact zone layout, field
codes, revision-table format per ISO 7200 or a company's drafting
standard) that a real engineering department would use. See the scope
boundary in `docs/architecture.md` (Phase 15) for why that distinction
matters and isn't attempted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from engine.geometry.primitives import Entity, LineEntity, Point3, RectangleEntity, TextEntity

_MARGIN = 10.0
_BLOCK_WIDTH = 80.0
_BLOCK_HEIGHT = 30.0
_ROW_HEIGHT = _BLOCK_HEIGHT / 3
_TEXT_HEIGHT = 2.5
_TEXT_PAD = 2.0


@dataclass(frozen=True)
class TemplateDefinition:
    name: str
    description: str
    width: float
    height: float


def _shift(point: Point3, origin: Point3) -> Point3:
    return (point[0] + origin[0], point[1] + origin[1], point[2] + origin[2])


def _translate(entities: List[Entity], origin: Point3) -> List[Entity]:
    if origin == (0.0, 0.0, 0.0):
        return entities
    translated: List[Entity] = []
    for entity in entities:
        if isinstance(entity, RectangleEntity):
            update = {"corner1": _shift(entity.corner1, origin), "corner2": _shift(entity.corner2, origin)}
            translated.append(entity.model_copy(update=update))
        elif isinstance(entity, LineEntity):
            update = {"start": _shift(entity.start, origin), "end": _shift(entity.end, origin)}
            translated.append(entity.model_copy(update=update))
        elif isinstance(entity, TextEntity):
            translated.append(entity.model_copy(update={"position": _shift(entity.position, origin)}))
        else:
            raise TypeError(f"unsupported entity type for template translation: {type(entity).__name__}")
    return translated


def build_title_block(
    template_name: str,
    title: str = "",
    drawn_by: str = "",
    date: str = "",
    scale: str = "",
    sheet_number: str = "",
    origin: Point3 = (0.0, 0.0, 0.0),
) -> List[Entity]:
    """Border rectangle + a 2-column-by-3-row title block in the bottom
    right corner: Title spans the top row; Drawn by/Date and Scale/Sheet
    split the middle and bottom rows. Every text field is optional — an
    empty string simply omits that label, so a caller can insert a bare
    border-and-grid without placeholder text."""
    definition = TEMPLATE_LIBRARY.get(template_name)
    if definition is None:
        raise KeyError(f"unknown template {template_name!r}; known templates: {sorted(TEMPLATE_LIBRARY)}")
    # Callers (REST/MCP tool arguments) may pass a 2-element [x, y] origin
    # with no z, unlike Point3 fields inside a validated Entity — normalize
    # once here so every downstream _shift() call can assume 3 elements.
    origin = (origin[0], origin[1], origin[2] if len(origin) > 2 else 0.0)

    w, h = definition.width, definition.height
    border = RectangleEntity(corner1=(_MARGIN, _MARGIN, 0.0), corner2=(w - _MARGIN, h - _MARGIN, 0.0))

    tb_x0, tb_y0 = w - _MARGIN - _BLOCK_WIDTH, _MARGIN
    tb_x1, tb_y1 = w - _MARGIN, _MARGIN + _BLOCK_HEIGHT
    title_block = RectangleEntity(corner1=(tb_x0, tb_y0, 0.0), corner2=(tb_x1, tb_y1, 0.0))

    row2_y = tb_y0 + _ROW_HEIGHT  # divider between bottom and middle rows
    row1_y = tb_y0 + 2 * _ROW_HEIGHT  # divider between middle and top (title) row
    mid_x = tb_x0 + _BLOCK_WIDTH / 2

    dividers = [
        LineEntity(start=(tb_x0, row2_y, 0.0), end=(tb_x1, row2_y, 0.0)),
        LineEntity(start=(tb_x0, row1_y, 0.0), end=(tb_x1, row1_y, 0.0)),
        LineEntity(start=(mid_x, tb_y0, 0.0), end=(mid_x, row1_y, 0.0)),
    ]

    fields = [
        (title, (tb_x0 + _TEXT_PAD, row1_y + _TEXT_PAD, 0.0)),
        (drawn_by, (tb_x0 + _TEXT_PAD, row2_y + _TEXT_PAD, 0.0)),
        (date, (mid_x + _TEXT_PAD, row2_y + _TEXT_PAD, 0.0)),
        (scale, (tb_x0 + _TEXT_PAD, tb_y0 + _TEXT_PAD, 0.0)),
        (sheet_number, (mid_x + _TEXT_PAD, tb_y0 + _TEXT_PAD, 0.0)),
    ]
    labels = ["Title", "Drawn by", "Date", "Scale", "Sheet"]
    texts = [
        TextEntity(position=position, text=f"{label}: {value}", height=_TEXT_HEIGHT)
        for label, (value, position) in zip(labels, fields)
        if value
    ]

    entities: List[Entity] = [border, title_block, *dividers, *texts]
    return _translate(entities, origin)


TEMPLATE_LIBRARY: Dict[str, TemplateDefinition] = {
    d.name: d
    for d in [
        TemplateDefinition("a4_landscape", "ISO A4 sheet, landscape", 297.0, 210.0),
        TemplateDefinition("a3_landscape", "ISO A3 sheet, landscape", 420.0, 297.0),
        TemplateDefinition("letter_landscape", "ANSI Letter sheet, landscape", 279.4, 215.9),
    ]
}
