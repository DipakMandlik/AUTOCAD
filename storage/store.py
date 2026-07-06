"""File-based ProjectStore: one JSON document per project."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from engine.geometry.primitives import DrawingPlan
from storage.models import Project, Revision

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


class ProjectNotFoundError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectStore:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str) -> Path:
        # project ids are also used to build a filename, so this doubles
        # as a path-traversal guard (mirrors cad.backend.resolve_safe_path).
        if not _SAFE_ID.match(project_id):
            raise ValueError(f"invalid project id: {project_id!r}")
        return self.root_dir / f"{project_id}.json"

    def create(self, name: str, plan: DrawingPlan) -> Project:
        now = _now()
        project = Project(
            id=uuid.uuid4().hex[:12],
            name=name,
            created_at=now,
            updated_at=now,
            plan=plan,
            revisions=[Revision(revision=1, created_at=now, plan=plan, note="initial snapshot")],
        )
        self._write(project)
        return project

    def get(self, project_id: str) -> Project:
        path = self._path(project_id)
        if not path.is_file():
            raise ProjectNotFoundError(project_id)
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> List[Project]:
        return [
            Project.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.root_dir.glob("*.json"))
        ]

    def add_revision(self, project_id: str, plan: DrawingPlan, note: Optional[str] = None) -> Project:
        project = self.get(project_id)
        now = _now()
        revision = Revision(revision=len(project.revisions) + 1, created_at=now, plan=plan, note=note)
        project.revisions.append(revision)
        project.plan = plan
        project.updated_at = now
        self._write(project)
        return project

    def _write(self, project: Project) -> None:
        self._path(project.id).write_text(project.model_dump_json(indent=2), encoding="utf-8")
