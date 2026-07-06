import pytest

from engine.geometry.primitives import (
    CircleEntity,
    EllipseEntity,
    LineEntity,
    PolylineEntity,
    RectangleEntity,
    TextEntity,
)
from imports.svg_import import SvgImportError, import_svg

SVG_HEADER = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'


def _wrap(body: str) -> str:
    return f"{SVG_HEADER}{body}</svg>"


def test_line_flips_y_against_viewbox_height():
    entities, warnings = import_svg(_wrap('<line x1="0" y1="0" x2="10" y2="20"/>'))
    assert warnings == []
    assert len(entities) == 1
    line = entities[0]
    assert isinstance(line, LineEntity)
    assert line.start == (0.0, 100.0, 0.0)
    assert line.end == (10.0, 80.0, 0.0)


def test_circle_requires_positive_radius():
    entities, warnings = import_svg(_wrap('<circle cx="10" cy="10" r="0"/><circle cx="5" cy="5" r="2"/>'))
    assert len(entities) == 1
    assert isinstance(entities[0], CircleEntity)
    assert len(warnings) == 1
    assert "circle" in warnings[0]


def test_rect_becomes_rectangle_with_flipped_corners():
    entities, _ = import_svg(_wrap('<rect x="10" y="10" width="20" height="5" id="box"/>'))
    rect = entities[0]
    assert isinstance(rect, RectangleEntity)
    assert rect.layer == "box"
    assert rect.corner1 == (10.0, 90.0, 0.0)
    assert rect.corner2 == (30.0, 85.0, 0.0)


def test_ellipse_swaps_axes_when_ry_larger():
    entities, _ = import_svg(_wrap('<ellipse cx="50" cy="50" rx="5" ry="10"/>'))
    ellipse = entities[0]
    assert isinstance(ellipse, EllipseEntity)
    assert ellipse.major_axis == 10.0
    assert ellipse.minor_axis == 5.0
    assert ellipse.rotation == 90.0


def test_polyline_and_polygon_closed_flag():
    entities, _ = import_svg(
        _wrap('<polyline points="0,0 10,0 10,10"/><polygon points="0,0 10,0 5,10"/>')
    )
    polyline, polygon = entities
    assert isinstance(polyline, PolylineEntity) and polyline.closed is False
    assert isinstance(polygon, PolylineEntity) and polygon.closed is True


def test_text_element_uses_font_size_as_height():
    entities, _ = import_svg(_wrap('<text x="1" y="1" font-size="4">hello</text>'))
    text = entities[0]
    assert isinstance(text, TextEntity)
    assert text.text == "hello"
    assert text.height == 4.0


def test_empty_text_element_produces_warning_not_crash():
    entities, warnings = import_svg(_wrap('<text x="1" y="1"></text><circle cx="1" cy="1" r="1"/>'))
    assert len(entities) == 1
    assert any("text" in w for w in warnings)


@pytest.mark.parametrize(
    "d,expected_points,expected_closed",
    [
        ("M0,0 L10,0 L10,10 Z", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], True),
        ("M0,0 H10 V10 Z", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], True),
        ("m0,0 l5,0 l0,5", [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)], False),
    ],
)
def test_path_straight_segment_commands(d, expected_points, expected_closed):
    entities, warnings = import_svg(_wrap(f'<path d="{d}"/>'))
    assert warnings == []
    path = entities[0]
    assert isinstance(path, PolylineEntity)
    assert path.closed is expected_closed
    # un-flip back to raw SVG space for a readable comparison against `d`
    raw = [(x, 100.0 - y) for x, y, _z in path.points]
    assert raw == pytest.approx(expected_points)


def test_path_with_curve_command_is_skipped_with_warning():
    entities, warnings = import_svg(
        _wrap('<path d="M0,0 C1,1 2,2 3,3"/><circle cx="5" cy="5" r="1"/>')
    )
    assert len(entities) == 1
    assert isinstance(entities[0], CircleEntity)
    assert len(warnings) == 1
    assert "curves aren't supported" in warnings[0]


def test_multiple_moveto_creates_multiple_subpaths():
    entities, _ = import_svg(_wrap('<path d="M0,0 L1,0 M5,5 L6,5"/>'))
    assert len(entities) == 2
    assert all(isinstance(e, PolylineEntity) for e in entities)


def test_unsupported_element_produces_warning_but_does_not_abort():
    entities, warnings = import_svg(_wrap('<foo/><circle cx="1" cy="1" r="1"/>'))
    assert len(entities) == 1
    assert warnings == ["unsupported element <foo> skipped"]


def test_structural_elements_are_silently_ignored():
    entities, warnings = import_svg(
        _wrap('<defs/><title>t</title><desc>d</desc><g><circle cx="1" cy="1" r="1"/></g>')
    )
    assert len(entities) == 1
    assert warnings == []


def test_invalid_xml_raises_svg_import_error():
    with pytest.raises(SvgImportError, match="invalid SVG/XML"):
        import_svg("<svg><line x1=0 /></svg>")


def test_doctype_is_rejected():
    with pytest.raises(SvgImportError, match="DOCTYPE"):
        import_svg('<?xml version="1.0"?><!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>')


def test_entity_declaration_is_rejected():
    with pytest.raises(SvgImportError, match="DOCTYPE"):
        import_svg('<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>')


def test_oversized_svg_is_rejected():
    huge = _wrap("<!-- padding -->" + "x" * 3_000_000)
    with pytest.raises(SvgImportError, match="byte import limit"):
        import_svg(huge)


def test_no_geometry_raises_svg_import_error():
    with pytest.raises(SvgImportError, match="no supported geometry"):
        import_svg(_wrap("<defs/>"))


def test_no_viewbox_or_height_falls_back_to_sign_flip():
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><line x1="0" y1="5" x2="0" y2="0"/></svg>'
    entities, _ = import_svg(svg)
    line = entities[0]
    assert line.start == (0.0, -5.0, 0.0)
    assert line.end == (0.0, 0.0, 0.0)


def test_height_attribute_used_when_no_viewbox():
    entities, _ = import_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" height="50px"><line x1="0" y1="10" x2="0" y2="0"/></svg>'
    )
    line = entities[0]
    assert line.start == (0.0, 40.0, 0.0)
