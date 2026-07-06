from engine.geometry.primitives import DrawingPlan
from engine.validator.engine import ValidationEngine


def test_valid_plan_has_no_errors():
    plan = DrawingPlan(
        operations=[
            {"type": "line", "start": [0, 0], "end": [10, 10]},
            {"type": "dimension", "start": [0, 0], "end": [10, 10]},
        ]
    )
    report = ValidationEngine().validate(plan)
    assert report.is_valid


def test_zero_length_line_flagged_and_autofixed():
    plan = DrawingPlan(operations=[{"type": "line", "start": [0, 0], "end": [0, 0]}])
    engine = ValidationEngine()
    report = engine.validate(plan)
    assert not report.is_valid
    assert any(i.code == "zero_length_line" for i in report.errors)

    fixed, applied = engine.autofix(plan)
    assert len(fixed.operations) == 0
    assert any(i.code == "zero_length_line" for i in applied)


def test_duplicate_entities_flagged_and_autofixed():
    entity = {"type": "circle", "center": [0, 0], "radius": 5}
    plan = DrawingPlan(operations=[entity, dict(entity)])
    engine = ValidationEngine()
    report = engine.validate(plan)
    assert any(i.code == "duplicate_entity" for i in report.warnings)

    fixed, applied = engine.autofix(plan)
    assert len(fixed.operations) == 1
    assert any(i.code == "duplicate_entity" for i in applied)


def test_invalid_layer_name_sanitized():
    plan = DrawingPlan(operations=[{"type": "circle", "center": [0, 0], "radius": 5, "layer": "wa/lls"}])
    engine = ValidationEngine()
    report = engine.validate(plan)
    assert any(i.code == "invalid_layer_name" for i in report.errors)

    fixed, applied = engine.autofix(plan)
    assert fixed.operations[0].layer == "wa_lls"
    assert any(i.code == "invalid_layer_name" for i in applied)


def test_overlapping_entities_on_same_layer_flagged():
    plan = DrawingPlan(
        operations=[
            {"type": "circle", "center": [0, 0], "radius": 10, "layer": "x"},
            {"type": "circle", "center": [5, 5], "radius": 10, "layer": "x"},
        ]
    )
    report = ValidationEngine().validate(plan)
    assert any(i.code == "possible_collision" for i in report.warnings)


def test_overlapping_entities_on_different_layers_not_flagged():
    plan = DrawingPlan(
        operations=[
            {"type": "circle", "center": [0, 0], "radius": 10, "layer": "x"},
            {"type": "circle", "center": [5, 5], "radius": 10, "layer": "y"},
        ]
    )
    report = ValidationEngine().validate(plan)
    assert not any(i.code == "possible_collision" for i in report.warnings)


def test_missing_dimensions_warns_but_does_not_invalidate():
    plan = DrawingPlan(operations=[{"type": "circle", "center": [0, 0], "radius": 5}])
    report = ValidationEngine().validate(plan)
    assert report.is_valid
    assert any(i.code == "missing_dimensions" for i in report.warnings)


def test_zero_sweep_arc_is_error_not_autofixed():
    plan = DrawingPlan(
        operations=[{"type": "arc", "center": [0, 0], "radius": 5, "start_angle": 45, "end_angle": 45}]
    )
    engine = ValidationEngine()
    report = engine.validate(plan)
    assert any(i.code == "zero_sweep_arc" and not i.autofixable for i in report.errors)
