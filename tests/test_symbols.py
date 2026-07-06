import pytest

from engine.geometry.primitives import (
    ArcEntity,
    CircleEntity,
    DrawingPlan,
    LineEntity,
    PolylineEntity,
    TextEntity,
)
from engine.validator.engine import ValidationEngine
from export.renderer import render_svg
from symbols.library import SYMBOL_LIBRARY, _transform


def test_transform_translates_by_origin():
    assert _transform([(0, 0)], (5, 5, 0), 1.0, 0.0) == [(5.0, 5.0, 0.0)]


def test_transform_scales_around_local_origin():
    assert _transform([(1, 0)], (0, 0, 0), 3.0, 0.0) == [(3.0, 0.0, 0.0)]


def test_transform_rotates_90_degrees():
    points = _transform([(1, 0)], (0, 0, 0), 1.0, 90.0)
    (x, y, _z) = points[0]
    assert x == pytest.approx(0.0, abs=1e-9)
    assert y == pytest.approx(1.0, abs=1e-9)


def test_transform_preserves_z_from_origin():
    assert _transform([(0, 0)], (1, 2, 7), 1.0, 0.0) == [(1.0, 2.0, 7.0)]


def test_catalog_keys_match_definition_names():
    for key, definition in SYMBOL_LIBRARY.items():
        assert key == definition.name
        assert definition.discipline
        assert definition.description


@pytest.mark.parametrize("name", sorted(SYMBOL_LIBRARY))
def test_every_symbol_validates_and_renders(name):
    definition = SYMBOL_LIBRARY[name]
    entities = definition.build((0.0, 0.0, 0.0), 1.0, 0.0)
    assert len(entities) >= 1

    plan = DrawingPlan(operations=entities)
    report = ValidationEngine().validate(plan)
    assert not report.errors, f"{name}: {report.errors}"

    svg = render_svg(plan)
    assert "<svg" in svg


def test_resistor_is_a_single_open_polyline():
    entities = SYMBOL_LIBRARY["resistor"].build((0, 0, 0), 1.0, 0.0)
    assert len(entities) == 1
    assert isinstance(entities[0], PolylineEntity)
    assert entities[0].closed is False


def test_gate_valve_is_two_closed_triangles():
    entities = SYMBOL_LIBRARY["gate_valve"].build((0, 0, 0), 1.0, 0.0)
    assert len(entities) == 2
    assert all(isinstance(e, PolylineEntity) and e.closed for e in entities)


def test_pump_is_circle_plus_triangle():
    entities = SYMBOL_LIBRARY["pump"].build((0, 0, 0), 1.0, 0.0)
    types = {type(e) for e in entities}
    assert types == {CircleEntity, PolylineEntity}


def test_door_swing_is_line_plus_arc():
    entities = SYMBOL_LIBRARY["door_swing"].build((0, 0, 0), 1.0, 0.0)
    types = {type(e) for e in entities}
    assert types == {LineEntity, ArcEntity}


def test_symbol_placement_respects_position_and_scale():
    entities = SYMBOL_LIBRARY["capacitor"].build((10.0, 20.0, 0.0), 2.0, 0.0)
    first_lead = entities[0]
    assert isinstance(first_lead, LineEntity)
    assert first_lead.start == (10.0, 20.0, 0.0)
    # local (0.45, 0) scaled by 2 and translated by (10, 20)
    assert first_lead.end == pytest.approx((10.9, 20.0, 0.0))


def test_symbol_rotation_rotates_arc_angles():
    unrotated = SYMBOL_LIBRARY["door_swing"].build((0, 0, 0), 1.0, 0.0)
    rotated = SYMBOL_LIBRARY["door_swing"].build((0, 0, 0), 1.0, 90.0)
    unrotated_arc = next(e for e in unrotated if isinstance(e, ArcEntity))
    rotated_arc = next(e for e in rotated if isinstance(e, ArcEntity))
    assert rotated_arc.start_angle == unrotated_arc.start_angle + 90.0
    assert rotated_arc.end_angle == unrotated_arc.end_angle + 90.0


def test_unknown_symbol_not_in_catalog():
    assert "not_a_real_symbol" not in SYMBOL_LIBRARY


def test_bearing_is_two_concentric_circles():
    entities = SYMBOL_LIBRARY["bearing"].build((0, 0, 0), 1.0, 0.0)
    assert len(entities) == 2
    assert all(isinstance(e, CircleEntity) for e in entities)
    radii = sorted(e.radius for e in entities)
    assert radii == pytest.approx([0.25, 0.5])
    assert entities[0].center == entities[1].center


def test_weld_symbol_is_line_plus_closed_triangle():
    entities = SYMBOL_LIBRARY["weld_symbol"].build((0, 0, 0), 1.0, 0.0)
    assert len(entities) == 2
    line = next(e for e in entities if isinstance(e, LineEntity))
    triangle = next(e for e in entities if isinstance(e, PolylineEntity))
    assert line.start == (0.0, 0.0, 0.0) and line.end == (1.0, 0.0, 0.0)
    assert triangle.closed is True
    assert len(triangle.points) == 3


def test_diffuser_is_closed_square_plus_two_diagonals():
    entities = SYMBOL_LIBRARY["diffuser"].build((0, 0, 0), 1.0, 0.0)
    assert len(entities) == 3
    square = next(e for e in entities if isinstance(e, PolylineEntity))
    assert square.closed is True
    assert len(square.points) == 4
    diagonals = [e for e in entities if isinstance(e, LineEntity)]
    assert len(diagonals) == 2


def test_thermostat_is_circle_plus_text():
    entities = SYMBOL_LIBRARY["thermostat"].build((0, 0, 0), 1.0, 0.0)
    types = {type(e) for e in entities}
    assert types == {CircleEntity, TextEntity}
    text = next(e for e in entities if isinstance(e, TextEntity))
    assert text.text == "T"


def test_column_is_two_nested_closed_squares():
    entities = SYMBOL_LIBRARY["column"].build((0, 0, 0), 1.0, 0.0)
    assert len(entities) == 2
    assert all(isinstance(e, PolylineEntity) and e.closed for e in entities)


def test_beam_is_three_lines_forming_an_i_shape():
    entities = SYMBOL_LIBRARY["beam"].build((0, 0, 0), 1.0, 0.0)
    assert len(entities) == 3
    assert all(isinstance(e, LineEntity) for e in entities)


def test_thermostat_text_scales_with_symbol_scale():
    entities = SYMBOL_LIBRARY["thermostat"].build((0, 0, 0), 2.0, 0.0)
    text = next(e for e in entities if isinstance(e, TextEntity))
    assert text.height == pytest.approx(0.7)  # local height 0.35 * scale 2
