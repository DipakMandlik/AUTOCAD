"""A bounded, in-memory audit trail of tool calls, shared by both transports.

Every MCP/REST tool call goes through a `ToolSpec.handler` in
`apps/server/tools.py`, which wraps each handler exactly once at import
time (see `_with_logging` there) so this stays a single cross-cutting
concern rather than something both `apps/server/server.py` and
`apps/api/main.py` would otherwise have to duplicate.

Scope: process-lifetime only, not persisted to disk — restarting the
server clears it, same as `ServerContext.history`. Capacity-bounded via a
`deque(maxlen=...)` so a long-running session can't grow this without
bound; the oldest entries are silently dropped once full, same tradeoff
`ProjectStore` and `history` make for "no unbounded state."
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional


@dataclass(frozen=True)
class ExecutionLogEntry:
    seq: int
    tool: str
    success: bool
    message: Optional[str]
    duration_ms: float
    timestamp: str


@dataclass(frozen=True)
class ToolStats:
    tool: str
    calls: int
    successes: int
    failures: int
    avg_duration_ms: float
    min_duration_ms: float
    max_duration_ms: float


class ExecutionLog:
    def __init__(self, capacity: int = 500) -> None:
        self._entries: Deque[ExecutionLogEntry] = deque(maxlen=capacity)
        self._next_seq = 1

    def record(self, tool: str, success: bool, message: Optional[str], duration_ms: float) -> None:
        entry = ExecutionLogEntry(
            seq=self._next_seq,
            tool=tool,
            success=success,
            message=message,
            duration_ms=duration_ms,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._next_seq += 1
        self._entries.append(entry)

    def recent(self, limit: int = 100) -> List[ExecutionLogEntry]:
        return list(self._entries)[-limit:]

    def clear(self) -> int:
        count = len(self._entries)
        self._entries.clear()
        return count

    def stats(self) -> List[ToolStats]:
        """Per-tool aggregates over whatever is currently in the bounded
        window — not a cumulative historical metric once eviction starts
        dropping entries, same caveat as `recent()`. Sorted by call count,
        most-called first, since that's the natural read order for a
        "what's hot" performance view."""
        by_tool: Dict[str, List[ExecutionLogEntry]] = defaultdict(list)
        for entry in self._entries:
            by_tool[entry.tool].append(entry)

        result = [
            ToolStats(
                tool=tool,
                calls=len(entries),
                successes=sum(1 for e in entries if e.success),
                failures=sum(1 for e in entries if not e.success),
                avg_duration_ms=sum(e.duration_ms for e in entries) / len(entries),
                min_duration_ms=min(e.duration_ms for e in entries),
                max_duration_ms=max(e.duration_ms for e in entries),
            )
            for tool, entries in by_tool.items()
        ]
        return sorted(result, key=lambda s: s.calls, reverse=True)

    def __len__(self) -> int:
        return len(self._entries)
