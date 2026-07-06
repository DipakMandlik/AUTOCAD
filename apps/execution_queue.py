"""A deferred, multi-tool execution queue.

`ServerContext.execution_log` (Phase 12) records what *has* happened;
this tracks what's *about to* happen. Enqueue any tool call by name
without running it, inspect or remove queued items, then run the whole
queue in one call. Each item runs independently — one item's failure
doesn't block the rest — which is the actual difference from
`/drawings/execute`'s atomic validate-or-nothing `DrawingPlan`: that one
is "all these entities as a single plan," this one is "a sequence of
independent tool calls, some of which may not even be geometry" (a
`create_layer` followed by a few `draw_*` calls followed by a
`save_drawing`, say).

Scope: process-lifetime, in-memory only, same as `execution_log` and
`history` — no persistence, no real concurrent job processing. "Running"
the queue executes items synchronously, one after another, in the same
request; there is no background worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class QueueItem:
    id: int
    tool: str
    arguments: Dict[str, Any]
    enqueued_at: str
    status: str = "queued"  # queued | succeeded | failed
    result: Optional[Dict[str, Any]] = None


@dataclass
class ExecutionQueue:
    _items: List[QueueItem] = field(default_factory=list)
    _next_id: int = 1

    def enqueue(self, tool: str, arguments: Dict[str, Any]) -> QueueItem:
        item = QueueItem(
            id=self._next_id,
            tool=tool,
            arguments=arguments,
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )
        self._next_id += 1
        self._items.append(item)
        return item

    def items(self) -> List[QueueItem]:
        return list(self._items)

    def get(self, item_id: int) -> Optional[QueueItem]:
        return next((i for i in self._items if i.id == item_id), None)

    def remove(self, item_id: int) -> bool:
        for i, item in enumerate(self._items):
            if item.id == item_id:
                del self._items[i]
                return True
        return False

    def clear(self) -> int:
        count = len(self._items)
        self._items.clear()
        return count

    def __len__(self) -> int:
        return len(self._items)
