"""Intent detection: the pluggable seam between raw text and the planner.

`FallbackIntentSource` (backed by `nlp.fallback.FallbackParser`) is the only
implementation today. An LLM-backed detector — the "AI Planning" layer in
the platform vision — can be added later as another class implementing
`IntentSource`, without the `Planner` or anything downstream changing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol

from nlp.fallback import FallbackParser


@dataclass
class Intent:
    operation: str
    params: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    source: str = "fallback_nlp"
    note: Optional[str] = None


class IntentSource(Protocol):
    def detect(self, text: str) -> Intent: ...


class FallbackIntentSource:
    """Adapts FallbackParser to the IntentSource interface."""

    source_name = "fallback_nlp"

    def __init__(self) -> None:
        self._parser = FallbackParser()

    def detect(self, text: str) -> Intent:
        parsed = self._parser.parse(text)
        return Intent(
            operation=parsed.operation,
            params=parsed.params,
            raw_text=text,
            source=self.source_name,
            note=parsed.note,
        )
