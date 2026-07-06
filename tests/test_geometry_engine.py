from engine.geometry.engine import GeometryEngine
from engine.geometry.primitives import ArcEntity, EllipseEntity, LineEntity, RectangleEntity

geometry = GeometryEngine()


def test_rectangle_corners_are_closed_loop():
    rect = RectangleEntity(corner1=[0, 0], corner2=[10, 5])
    corners = geometry.rectangle_corners(rect)
    assert corners[0] == corners[-1]
    assert len(corners) == 5


def test_line_bounding_box():
    line = LineEntity(start=[0, 0], end=[10, 5])
    bbox = geometry.bounding_box(line)
    assert (bbox.min_x, bbox.min_y, bbox.max_x, bbox.max_y) == (0.0, 0.0, 10.0, 5.0)


def test_arc_bounding_box_includes_crossed_cardinal_angle():
    # sweeps through 0 degrees, so max_x should reach the full radius
    arc = ArcEntity(center=[0, 0], radius=10, start_angle=350, end_angle=10)
    bbox = geometry.bounding_box(arc)
    assert bbox.max_x == 10.0


def test_arc_bounding_box_excludes_uncrossed_cardinal_angle():
    # a small arc entirely in the first quadrant should not reach x=radius
    arc = ArcEntity(center=[0, 0], radius=10, start_angle=30, end_angle=60)
    bbox = geometry.bounding_box(arc)
    assert bbox.max_x < 10.0


def test_ellipse_bounding_box_axis_aligned():
    ellipse = EllipseEntity(center=[0, 0], major_axis=10, minor_axis=5, rotation=0)
    bbox = geometry.bounding_box(ellipse)
    assert bbox.max_x == 10.0
    assert bbox.max_y == 5.0


def test_plan_bounding_box_of_empty_list_is_none():
    assert geometry.plan_bounding_box([]) is None


def test_overlap_detection():
    a = geometry.bounding_box(LineEntity(start=[0, 0], end=[10, 10]))
    b = geometry.bounding_box(LineEntity(start=[5, 5], end=[15, 15]))
    c = geometry.bounding_box(LineEntity(start=[20, 20], end=[30, 30]))
    assert a.overlaps(b)
    assert not a.overlaps(c)
