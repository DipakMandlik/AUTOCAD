"""Render a DrawingPlan to SVG or PNG.

SVG rendering uses ezdxf's native SVG backend (Pillow is its only
dependency, pulled in transitively by `ezdxf.addons.drawing`) and is
always available. PNG rendering additionally needs matplotlib, which is an
optional extra (`pip install -e ".[render-png]"`) — `render_png` raises a
clear `RuntimeError` if it isn't installed, the same pattern as
`autocad.backend`'s guarded `pywin32` import.
"""

from __future__ import annotations

import io

from ezdxf.addons.drawing import Frontend, RenderContext, layout
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.math import BoundingBox2d

from dxf.backend import DXFBackend
from engine.geometry.primitives import DrawingPlan


def _build_document(plan: DrawingPlan):
    # output_dir is irrelevant here: this renderer never calls .save().
    renderer = DXFBackend(output_dir=".")
    renderer.execute(plan)
    return renderer.doc


def render_svg(plan: DrawingPlan) -> str:
    doc = _build_document(plan)
    msp = doc.modelspace()
    context = RenderContext(doc)
    backend = SVGBackend()
    Frontend(context, backend).draw_layout(msp)

    if len(msp):
        # Page(0, 0) means "auto-fit to content."
        return backend.get_string(layout.Page(0, 0))

    # An empty plan has no content bounding box, and ezdxf raises
    # ValueError trying to compute one for placement — even with a fixed
    # (non-auto-fit) page size, it still needs *some* box. Supply a
    # trivial explicit one for the (valid, if uninteresting) case of
    # rendering nothing.
    return backend.get_string(layout.Page(100, 100), render_box=BoundingBox2d([(0, 0), (1, 1)]))


def render_png(plan: DrawingPlan, dpi: int = 150) -> bytes:
    try:
        import matplotlib
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    except ImportError as exc:
        raise RuntimeError(
            "PNG rendering requires matplotlib: pip install -e '.[render-png]'"
        ) from exc
    matplotlib.use("Agg")

    doc = _build_document(plan)
    fig = plt.figure()
    try:
        ax = fig.add_axes((0, 0, 1, 1))
        context = RenderContext(doc)
        backend = MatplotlibBackend(ax)
        Frontend(context, backend).draw_layout(doc.modelspace())
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=dpi)
        buffer.seek(0)
        return buffer.read()
    finally:
        plt.close(fig)
