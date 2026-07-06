import pytest

from engine.geometry.primitives import DrawingPlan
from export.renderer import render_png, render_svg


@pytest.fixture
def plan():
    return DrawingPlan(
        operations=[
            {"type": "circle", "center": [0, 0], "radius": 5},
            {"type": "line", "start": [0, 0], "end": [10, 10]},
        ]
    )


def test_render_svg_produces_valid_svg_markup(plan):
    svg = render_svg(plan)
    assert svg.startswith("<?xml")
    assert "<svg" in svg
    assert "</svg>" in svg


def test_render_svg_of_empty_plan_does_not_raise():
    svg = render_svg(DrawingPlan())
    assert "<svg" in svg


def test_render_png_produces_valid_png_bytes(plan):
    png = render_png(plan)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100


def test_render_png_raises_clear_error_without_matplotlib(plan, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "matplotlib":
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="matplotlib"):
        render_png(plan)


def test_render_does_not_touch_the_live_backend_document(plan, tmp_path):
    # rendering builds its own throwaway ezdxf document; it must not read
    # from or write to whatever CADBackend is actually live for execution.
    from dxf.backend import DXFBackend

    live_backend = DXFBackend(output_dir=str(tmp_path))
    live_backend.execute(DrawingPlan(operations=[{"type": "circle", "center": [99, 99], "radius": 1}]))
    entity_count_before = len(live_backend.doc.modelspace())

    render_svg(plan)
    render_png(plan)

    assert len(live_backend.doc.modelspace()) == entity_count_before
