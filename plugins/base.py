"""The Plugin data shape. Plugin authors import this and nothing else
from this package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List

from apps.server.tools import ToolSpec
from engine.validator.rules import RuleFn


@dataclass
class Plugin:
    name: str
    version: str = "0.1.0"
    tools: List[ToolSpec] = field(default_factory=list)
    validation_rules: List[RuleFn] = field(default_factory=list)
    backends: Dict[str, Callable] = field(default_factory=dict)
