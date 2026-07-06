"""Rendering a DrawingPlan to SVG/PNG for preview and export.

This is a rendering concern, not an execution concern: it builds its own
throwaway in-memory ezdxf document via `DXFBackend` regardless of which
`CADBackend` is actually live, so "render me an SVG of this plan" works
the same way whether the configured execution backend is `dxf` or
`autocad`. Nothing here is saved to disk as a drawing — `dxf.backend`
already owns that.
"""
