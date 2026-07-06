import pytest

from engine.planner.planner import NonGeometryIntent, Planner, PlanningError


def test_plan_from_text_builds_line_entity():
    planner = Planner()
    plan = planner.plan_from_text("draw a line from (0,0) to (10,10)")
    assert len(plan.operations) == 1
    assert plan.operations[0].type == "line"
    assert plan.source_text == "draw a line from (0,0) to (10,10)"


def test_plan_from_operation_applies_default_layer():
    planner = Planner(default_layer="walls")
    plan = planner.plan_from_operation("draw_circle", {"center": [0, 0], "radius": 5})
    assert plan.operations[0].layer == "walls"


def test_plan_from_operation_ignores_none_values():
    planner = Planner()
    plan = planner.plan_from_operation(
        "draw_line", {"start": [0, 0], "end": [1, 1], "layer": None, "color": None, "lineweight": None}
    )
    entity = plan.operations[0]
    assert entity.layer == "0"
    assert entity.color is None


def test_save_intent_raises_non_geometry_intent():
    planner = Planner()
    with pytest.raises(NonGeometryIntent) as exc_info:
        planner.plan_from_text('save the drawing to "out.dxf"')
    assert exc_info.value.operation == "save"
    assert exc_info.value.params["file_path"] == "out.dxf"


def test_create_layer_intent_raises_non_geometry_intent():
    planner = Planner()
    with pytest.raises(NonGeometryIntent) as exc_info:
        planner.plan_from_text("创建图层 walls")
    assert exc_info.value.operation == "create_layer"


def test_unrecognized_text_raises_planning_error():
    planner = Planner()
    with pytest.raises(PlanningError):
        planner.plan_from_text("do something incomprehensible")


def test_invalid_parameters_raise_planning_error_not_pydantic_error():
    planner = Planner()
    with pytest.raises(PlanningError):
        planner.plan_from_operation("draw_circle", {"center": [0, 0], "radius": -5})


def test_unknown_operation_raises_planning_error():
    planner = Planner()
    with pytest.raises(PlanningError):
        planner.plan_from_operation("levitate", {})
