"""COM-driven CADBackend for AutoCAD, GstarCAD, and ZWCAD.

Windows-only (requires pywin32 and a licensed, installed CAD application).
This package cannot be imported, let alone exercised, outside Windows —
`cad.registry` only imports it lazily, when the "autocad"/"gcad"/
"gstarcad"/"zwcad" backend is actually selected, so the rest of the
platform (including the full test suite) never needs pywin32 installed.
"""
