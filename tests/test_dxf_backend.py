import ezdxf
import pytest

from cad.registry import get_backend
from engine.geometry.primitives import DrawingPlan


@pytest.fixture
def backend(tmp_path):
    return get_backend("dxf", output_dir=str(tmp_path))


def test_draws_every_entity_type_and_produces_valid_dxf(backend, tmp_path):
    plan = DrawingPlan(
        operations=[
            {"type": "line", "start": [0, 0], "end": [100, 0], "layer": "walls"},
            {"type": "circle", "center": [50, 50], "radius": 20, "color": 1},
            {"type": "arc", "center": [0, 0], "radius": 30, "start_angle": 0, "end_angle": 180},
            {"type": "rectangle", "corner1": [0, 0], "corner2": [10, 10], "layer": "frame"},
            {"type": "ellipse", "center": [20, 20], "major_axis": 10, "minor_axis": 5, "rotation": 30},
            {"type": "polyline", "points": [[0, 0], [10, 0], [10, 10]], "closed": True},
            {"type": "text", "position": [5, 5], "text": "Hello CAD", "height": 3},
            {"type": "hatch", "points": [[0, 0], [10, 0], [10, 10]], "pattern_name": "SOLID"},
            {"type": "dimension", "start": [0, 0], "end": [100, 0]},
        ]
    )

    result = backend.execute(plan)
    assert result.success
    assert len(result.results) == 9
    assert all(r.handle for r in result.results)

    backend.save("demo.dxf")
    output_file = tmp_path / "demo.dxf"
    assert output_file.exists()

    doc = ezdxf.readfile(str(output_file))
    msp = doc.modelspace()
    assert len(msp) == 9
    layer_names = {layer.dxf.name for layer in doc.layers}
    assert {"walls", "frame"} <= layer_names


def test_unsupported_entity_type_fails_gracefully(backend):
    # exercised indirectly: draw_entity should never raise for a known
    # entity type; this checks the "unsupported type" branch directly.
    from engine.geometry.primitives import LineEntity

    line = LineEntity(start=[0, 0], end=[1, 1])
    line.type = "spline"  # simulate an unhandled type
    result = backend.draw_entity(line)
    assert not result.success
    assert "unsupported entity type" in result.error


def test_save_rejects_path_traversal(backend):
    with pytest.raises(ValueError):
        backend.save("../../etc/passwd")


def test_save_allows_absolute_path_inside_output_dir(backend, tmp_path):
    target = tmp_path / "nested" / "drawing.dxf"
    assert backend.save(str(target))
    assert target.exists()


def test_layer_created_once_and_cached(backend):
    assert backend.create_layer("walls")
    assert "walls" in backend.doc.layers
    # calling again must not raise (ezdxf raises if you re-add a layer with
    # the same name) — this is exactly the cache the original repo lacked.
    assert backend.create_layer("walls")
