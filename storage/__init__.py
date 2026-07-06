"""Project persistence: named, versioned snapshots of a DrawingPlan.

File-based (one JSON document per project) rather than a database — at
this scale a directory of JSON files is a perfectly adequate store, and it
keeps the platform dependency-free. `ProjectStore` is the seam: swapping to
a real database later means replacing this module, not anything that
calls it, the same interface-over-adapter pattern as `cad.backend`.
"""
