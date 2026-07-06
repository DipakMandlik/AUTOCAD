from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from engine.geometry.primitives import DrawingPlan


class Revision(BaseModel):
    revision: int
    created_at: str
    plan: DrawingPlan
    note: Optional[str] = None


class Project(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    plan: DrawingPlan
    revisions: List[Revision] = Field(default_factory=list)
