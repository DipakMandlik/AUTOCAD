"""ValidationEngine: runs rules over a DrawingPlan and can autofix safe issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from engine.geometry.engine import GeometryEngine
from engine.geometry.primitives import DrawingPlan
from engine.validator.issue import Issue
from engine.validator.rules import DEFAULT_RULES, INVALID_LAYER_CHARS, RuleFn


@dataclass
class ValidationReport:
    issues: List[Issue] = field(default_factory=list)

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return not self.errors


class ValidationEngine:
    def __init__(self, rules: Optional[List[RuleFn]] = None, geometry: Optional[GeometryEngine] = None):
        self.rules = rules if rules is not None else list(DEFAULT_RULES)
        self.geometry = geometry or GeometryEngine()

    def validate(self, plan: DrawingPlan) -> ValidationReport:
        issues: List[Issue] = []
        for rule in self.rules:
            issues.extend(rule(plan, self.geometry))
        return ValidationReport(issues=issues)

    def autofix(self, plan: DrawingPlan) -> Tuple[DrawingPlan, List[Issue]]:
        """Apply mechanical fixes for issues explicitly marked autofixable.

        Duplicate entities are dropped, zero-length lines are dropped, and
        invalid layer names are sanitized. Everything else (overlaps,
        missing dimensions, degenerate polylines, zero-sweep arcs, empty
        text) requires a judgment call and is left for the caller to
        resolve deliberately.
        """
        report = self.validate(plan)
        applied: List[Issue] = []
        operations = list(plan.operations)
        drop_indices: Set[int] = set()

        for issue in report.issues:
            if not issue.autofixable:
                continue
            if issue.code in ("duplicate_entity", "zero_length_line"):
                drop_indices.add(issue.indices[-1])
                applied.append(issue)
            elif issue.code == "invalid_layer_name":
                idx = issue.indices[0]
                entity = operations[idx]
                sanitized = INVALID_LAYER_CHARS.sub("_", entity.layer)
                operations[idx] = entity.model_copy(update={"layer": sanitized})
                applied.append(issue)

        operations = [op for i, op in enumerate(operations) if i not in drop_indices]
        fixed_plan = plan.model_copy(update={"operations": operations})
        return fixed_plan, applied
