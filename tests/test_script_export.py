import pytest

from engine.geometry.primitives import DrawingPlan
from export.script import render_lisp, render_scr, unsupported_entities


def test_line_commands():
    plan = DrawingPlan(operations=[{"type": "line", "start": [0, 0], "end": [10, 10]}])
    scr = render_scr(plan)
    assert scr.splitlines() == ["LINE", "0,0", "10,10", ""]

    lisp = render_lisp(plan)
    assert lisp.strip() == '(command "LINE" \'(0 0) \'(10 10) "")'


def test_circle_commands():
    plan = DrawingPlan(operations=[{"type": "circle", "center": [1, 2], "radius": 5}])
    scr = render_scr(plan)
    assert scr.splitlines() == ["CIRCLE", "1,2", "5"]


def test_arc_commands_use_center_start_angle_option():
    plan = DrawingPlan(
        operations=[{"type": "arc", "center": [0, 0], "radius": 10, "start_angle": 0, "end_angle": 90}]
    )
    scr = render_scr(plan)
    lines = scr.splitlines()
    assert lines[0] == "ARC"
    assert lines[1] == "C"
    assert lines[2] == "0,0"
    assert lines[3] == "10,0"  # point on the circle at start_angle=0
    assert lines[4] == "A"
    assert lines[5] == "90"  # included sweep angle


def test_arc_sweep_wraps_through_zero_degrees():
    plan = DrawingPlan(
        operations=[{"type": "arc", "center": [0, 0], "radius": 10, "start_angle": 350, "end_angle": 10}]
    )
    lines = render_scr(plan).splitlines()
    assert lines[5] == "20"  # (10 - 350) % 360 == 20


def test_ellipse_commands_use_center_option():
    plan = DrawingPlan(
        operations=[{"type": "ellipse", "center": [0, 0], "major_axis": 10, "minor_axis": 5, "rotation": 0}]
    )
    lines = render_scr(plan).splitlines()
    assert lines[0:4] == ["ELLIPSE", "C", "0,0", "10,0"]
    assert lines[4] == "5"


def test_polyline_open_ends_with_blank_and_closed_ends_with_c():
    open_plan = DrawingPlan(operations=[{"type": "polyline", "points": [[0, 0], [1, 1]], "closed": False}])
    assert render_scr(open_plan).splitlines()[-1] == ""

    closed_plan = DrawingPlan(operations=[{"type": "polyline", "points": [[0, 0], [1, 1]], "closed": True}])
    assert render_scr(closed_plan).splitlines()[-1] == "C"


def test_rectangle_commands():
    plan = DrawingPlan(operations=[{"type": "rectangle", "corner1": [0, 0], "corner2": [5, 5]}])
    assert render_scr(plan).splitlines() == ["RECTANG", "0,0", "5,5"]


def test_text_commands_include_trailing_blank_to_close_multiline_loop():
    plan = DrawingPlan(operations=[{"type": "text", "position": [0, 0], "text": "hello", "height": 2.5}])
    lines = render_scr(plan).splitlines()
    assert lines[0] == "TEXT"
    assert lines[4] == "hello"
    assert lines[5] == ""


def test_dimension_uses_computed_default_text_position_when_omitted():
    plan = DrawingPlan(operations=[{"type": "dimension", "start": [0, 0], "end": [10, 0]}])
    lines = render_scr(plan).splitlines()
    assert lines[0] == "DIMALIGNED"
    assert lines[3] == "5,5"  # midpoint (5,0) offset +5 in y


def test_dimension_uses_explicit_text_position():
    plan = DrawingPlan(
        operations=[{"type": "dimension", "start": [0, 0], "end": [10, 0], "text_position": [5, 20]}]
    )
    lines = render_scr(plan).splitlines()
    assert lines[3] == "5,20"


def test_hatch_is_skipped_not_guessed():
    plan = DrawingPlan(operations=[{"type": "hatch", "points": [[0, 0], [10, 0], [10, 10]]}])
    assert render_scr(plan) == "\n"
    assert render_lisp(plan) == "\n"
    assert unsupported_entities(plan) == [0]


def test_unsupported_entities_reports_correct_indices_in_mixed_plan():
    plan = DrawingPlan(
        operations=[
            {"type": "circle", "center": [0, 0], "radius": 5},
            {"type": "hatch", "points": [[0, 0], [10, 0], [10, 10]]},
            {"type": "line", "start": [0, 0], "end": [1, 1]},
        ]
    )
    assert unsupported_entities(plan) == [1]
    # the hatch is skipped but the surrounding entities still render
    scr_lines = render_scr(plan).splitlines()
    assert "CIRCLE" in scr_lines
    assert "LINE" in scr_lines


def test_empty_plan_renders_empty_but_valid_output():
    plan = DrawingPlan()
    assert render_scr(plan) == "\n"
    assert render_lisp(plan) == "\n"


def test_number_formatting_avoids_scientific_notation_and_trailing_zeros():
    plan = DrawingPlan(operations=[{"type": "circle", "center": [0.0, 0.0], "radius": 5.25}])
    lines = render_scr(plan).splitlines()
    assert lines == ["CIRCLE", "0,0", "5.25"]


def test_negative_coordinates():
    plan = DrawingPlan(operations=[{"type": "line", "start": [-5, -5], "end": [5, 5]}])
    lines = render_scr(plan).splitlines()
    assert lines[1] == "-5,-5"


def test_lisp_quotes_string_tokens_but_not_numbers():
    plan = DrawingPlan(operations=[{"type": "circle", "center": [0, 0], "radius": 5}])
    lisp = render_lisp(plan)
    assert '"CIRCLE"' in lisp
    assert "'(0 0)" in lisp
    assert " 5)" in lisp  # bare number, not quoted


@pytest.mark.parametrize(
    "entity_type", ["line", "circle", "arc", "ellipse", "polyline", "rectangle", "text", "dimension"]
)
def test_every_supported_entity_type_produces_nonempty_output(entity_type):
    samples = {
        "line": {"type": "line", "start": [0, 0], "end": [1, 1]},
        "circle": {"type": "circle", "center": [0, 0], "radius": 1},
        "arc": {"type": "arc", "center": [0, 0], "radius": 1, "start_angle": 0, "end_angle": 90},
        "ellipse": {"type": "ellipse", "center": [0, 0], "major_axis": 2, "minor_axis": 1},
        "polyline": {"type": "polyline", "points": [[0, 0], [1, 1]]},
        "rectangle": {"type": "rectangle", "corner1": [0, 0], "corner2": [1, 1]},
        "text": {"type": "text", "position": [0, 0], "text": "x"},
        "dimension": {"type": "dimension", "start": [0, 0], "end": [1, 0]},
    }
    plan = DrawingPlan(operations=[samples[entity_type]])
    assert render_scr(plan).strip() != ""
    assert render_lisp(plan).strip() != ""
    assert unsupported_entities(plan) == []
