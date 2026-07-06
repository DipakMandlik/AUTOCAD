"""CADBackend: the interface every execution target implements.

`DXFBackend` (headless, ezdxf-based) and `AutoCADBackend` (Windows COM) are
both adapters against this interface. Adding a new target — ZWCAD-specific
quirks, or eventually FreeCAD/Fusion 360 — means writing a new class here,
not branching inside an existing one.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from engine.geometry.primitives import DrawingPlan, Entity


def resolve_safe_path(output_dir: str, file_path: str) -> str:
    """Resolve file_path against output_dir, refusing any path that would
    escape it.

    The original repo passed user/LLM-supplied paths straight to
    `Document.SaveAs` with no containment check, so a prompt like
    "save to ../../etc/whatever" would be honored as-is. Every backend's
    `save()` must route through this.
    """
    root = os.path.abspath(output_dir)
    candidate = os.path.abspath(file_path if os.path.isabs(file_path) else os.path.join(root, file_path))
    if os.path.commonpath([root, candidate]) != root:
        raise ValueError(f"refusing to write outside the configured output directory: {file_path}")
    return candidate


@dataclass
class EntityResult:
    entity_type: str
    success: bool
    index: int = -1
    handle: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ExecutionResult:
    success: bool
    results: List[EntityResult] = field(default_factory=list)

    @property
    def failed(self) -> List[EntityResult]:
        return [r for r in self.results if not r.success]


class CADBackend(ABC):
    """Common interface for every CAD execution target."""

    name: str = "backend"

    @abstractmethod
    def start(self) -> bool:
        """Ensure the backend is ready to receive drawing commands."""

    @abstractmethod
    def is_running(self) -> bool:
        ...

    @abstractmethod
    def create_layer(self, layer_name: str) -> bool:
        ...

    @abstractmethod
    def draw_entity(self, entity: Entity) -> EntityResult:
        """Draw a single entity. Implementations should cache known layers
        internally rather than re-querying the backend on every call — the
        original code did an O(n) layer scan per drawn entity."""

    @abstractmethod
    def save(self, file_path: str) -> bool:
        ...

    def execute(self, plan: DrawingPlan) -> ExecutionResult:
        """Run every operation in a DrawingPlan against this backend.

        A failure on one entity does not abort the rest of the plan —
        partial completion with a per-entity error report is more useful
        than an all-or-nothing failure for a multi-hundred-entity drawing.
        """
        if not self.is_running():
            self.start()

        results: List[EntityResult] = []
        for index, entity in enumerate(plan.operations):
            try:
                self.create_layer(entity.layer)
                result = self.draw_entity(entity)
            except Exception as exc:  # noqa: BLE001 - backend calls (COM, file I/O) can fail in many ways
                result = EntityResult(entity_type=entity.type, success=False, error=str(exc))
            result.index = index
            results.append(result)

        return ExecutionResult(success=all(r.success for r in results), results=results)
