import pytest
from pydantic import ValidationError

from engine.geometry.primitives import CircleEntity, DrawingPlan, LineEntity


def test_point_coerces_2d_to_3d():
    line = LineEntity(start=[0, 0], end=(10, 10))
    assert line.start == (0.0, 0.0, 0.0)
    assert line.end == (10.0, 10.0, 0.0)


def test_point_keeps_explicit_z():
    line = LineEntity(start=[0, 0, 5], end=[10, 10, 5])
    assert line.start == (0.0, 0.0, 5.0)


def test_radius_must_be_positive():
    with pytest.raises(ValidationError):
        CircleEntity(center=[0, 0], radius=0)
    with pytest.raises(ValidationError):
        CircleEntity(center=[0, 0], radius=-5)


def test_invalid_lineweight_rejected():
    with pytest.raises(ValidationError):
        LineEntity(start=[0, 0], end=[1, 1], lineweight=7)


def test_valid_lineweight_accepted():
    line = LineEntity(start=[0, 0], end=[1, 1], lineweight=25)
    assert line.lineweight == 25


def test_drawing_plan_discriminates_entity_types():
    plan = DrawingPlan(
        operations=[
            {"type": "line", "start": [0, 0], "end": [1, 1]},
            {"type": "circle", "center": [0, 0], "radius": 5},
        ]
    )
    assert isinstance(plan.operations[0], LineEntity)
    assert isinstance(plan.operations[1], CircleEntity)


def test_color_out_of_range_rejected():
    with pytest.raises(ValidationError):
        LineEntity(start=[0, 0], end=[1, 1], color=999)
