"""Multi-format import: turn external file formats into a `DrawingPlan`.

Only SVG is supported so far — see `svg_import.py` for the exact subset of
SVG this understands and what it deliberately doesn't (curves, transforms,
style/color mapping). PDF/image/sketch/Excel import remain deferred.
"""
