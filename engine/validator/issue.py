from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal

Severity = Literal["error", "warning"]


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str
    indices: List[int] = field(default_factory=list)
    autofixable: bool = False
