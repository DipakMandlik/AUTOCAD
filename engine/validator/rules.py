"""Individual validation rules.

Each rule is a plain function `(plan, geometry) -> list[Issue]` so new
checks can be added by writing a function and appending it to
`DEFAULT_RULES`, without touching `ValidationEngine`.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List

from engine.geometry.engine import GeometryEngine
from engine.geometry.primitives import (
    ArcEntity,
    DimensionEntity,
    DrawingPlan,
    LineEntity,
    PolylineEntity,
    TextEntity,
)
from engine.validator.issue import Issue

# Characters AutoCAD (and most CAD apps) disallow in layer names.
INVALID_LAYER_CHARS = re.compile(r'[<>/\\":;?*|=,`]')

RuleFn = Callable[[DrawingPlan, GeometryEngine], List[Issue]]


def rule_zero_length_line(plan: DrawingPlan, geometry: GeometryEngine) -> List[Issue]:
    issues = []
    for i, entity in enumerate(plan.operations):
        if isinstance(entity, LineEntity) and entity.start == entity.end:
            issues.append(
                Issue(
                    severity="error",
                    code="zero_length_line",
                    message=f"line at index {i} has identical start and end points",
                    indices=[i],
                    autofixable=True,
                )
            )
    return issues


def rule_degenerate_polyline(plan: DrawingPlan, geometry: GeometryEngine) -> List[Issue]:
    issues = []
    for i, entity in enumerate(plan.operations):
        if isinstance(entity, PolylineEntity) and len(set(entity.points)) < 2:
            issues.append(
                Issue(
                    severity="error",
                    code="degenerate_polyline",
                    message=f"polyline at index {i} has fewer than 2 distinct points",
                    indices=[i],
                )
            )
    return issues


def rule_invalid_arc_sweep(plan: DrawingPlan, geometry: GeometryEngine) -> List[Issue]:
    issues = []
    for i, entity in enumerate(plan.operations):
        if isinstance(entity, ArcEntity) and entity.start_angle % 360 == entity.end_angle % 360:
            issues.append(
                Issue(
                    severity="error",
                    code="zero_sweep_arc",
                    message=f"arc at index {i} has identical start and end angles (zero sweep)",
                    indices=[i],
                )
            )
    return issues


def rule_empty_text(plan: DrawingPlan, geometry: GeometryEngine) -> List[Issue]:
    issues = []
    for i, entity in enumerate(plan.operations):
        if isinstance(entity, TextEntity) and not entity.text.strip():
            issues.append(
                Issue(
                    severity="error",
                    code="empty_text",
                    message=f"text entity at index {i} has blank content",
                    indices=[i],
                )
            )
    return issues


def rule_invalid_layer_name(plan: DrawingPlan, geometry: GeometryEngine) -> List[Issue]:
    issues = []
    for i, entity in enumerate(plan.operations):
        if INVALID_LAYER_CHARS.search(entity.layer):
            issues.append(
                Issue(
                    severity="error",
                    code="invalid_layer_name",
                    message=(
                        f"layer {entity.layer!r} at index {i} contains characters "
                        "not allowed by standard CAD layer naming rules"
                    ),
                    indices=[i],
                    autofixable=True,
                )
            )
    return issues


def rule_duplicate_entities(plan: DrawingPlan, geometry: GeometryEngine) -> List[Issue]:
    issues = []
    seen: Dict[str, int] = {}
    for i, entity in enumerate(plan.operations):
        key = f"{entity.type}:{entity.model_dump_json()}"
        if key in seen:
            issues.append(
                Issue(
                    severity="warning",
                    code="duplicate_entity",
                    message=f"entity at index {i} duplicates entity at index {seen[key]}",
                    indices=[seen[key], i],
                    autofixable=True,
                )
            )
        else:
            seen[key] = i
    return issues


def rule_overlapping_entities(plan: DrawingPlan, geometry: GeometryEngine) -> List[Issue]:
    # O(n^2) bounding-box comparison: fine for the plan sizes this MVP
    # targets, but would need spatial indexing before it scales to the
    # platform's eventual 100k-entity goal.
    issues = []
    boxes = [(i, e.layer, geometry.bounding_box(e)) for i, e in enumerate(plan.operations)]
    for a in range(len(boxes)):
        i, layer_a, box_a = boxes[a]
        for b in range(a + 1, len(boxes)):
            j, layer_b, box_b = boxes[b]
            if layer_a == layer_b and box_a.overlaps(box_b):
                issues.append(
                    Issue(
                        severity="warning",
                        code="possible_collision",
                        message=(
                            f"entities at index {i} and {j} on layer {layer_a!r} "
                            "have overlapping bounding boxes"
                        ),
                        indices=[i, j],
                    )
                )
    return issues


def rule_missing_dimensions(plan: DrawingPlan, geometry: GeometryEngine) -> List[Issue]:
    has_geometry = any(not isinstance(e, DimensionEntity) for e in plan.operations)
    has_dimension = any(isinstance(e, DimensionEntity) for e in plan.operations)
    if has_geometry and not has_dimension:
        return [
            Issue(
                severity="warning",
                code="missing_dimensions",
                message="drawing contains geometry but no dimension entities",
            )
        ]
    return []


DEFAULT_RULES: List[RuleFn] = [
    rule_zero_length_line,
    rule_degenerate_polyline,
    rule_invalid_arc_sweep,
    rule_empty_text,
    rule_invalid_layer_name,
    rule_duplicate_entities,
    rule_overlapping_entities,
    rule_missing_dimensions,
]
