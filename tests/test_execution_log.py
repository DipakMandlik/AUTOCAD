from apps.execution_log import ExecutionLog


def test_record_and_recent_preserve_order():
    log = ExecutionLog()
    log.record("draw_circle", True, "drawn", 1.0)
    log.record("draw_line", False, "failed to draw entity", 2.0)
    entries = log.recent()
    assert [e.tool for e in entries] == ["draw_circle", "draw_line"]
    assert entries[0].success is True
    assert entries[1].success is False


def test_seq_is_monotonic_and_never_reused():
    log = ExecutionLog()
    for _ in range(5):
        log.record("draw_circle", True, "drawn", 0.5)
    seqs = [e.seq for e in log.recent()]
    assert seqs == [1, 2, 3, 4, 5]


def test_capacity_evicts_oldest_first():
    log = ExecutionLog(capacity=3)
    for i in range(5):
        log.record(f"tool_{i}", True, None, 0.1)
    entries = log.recent(limit=10)
    assert [e.tool for e in entries] == ["tool_2", "tool_3", "tool_4"]
    assert len(log) == 3


def test_recent_respects_limit():
    log = ExecutionLog()
    for i in range(10):
        log.record(f"tool_{i}", True, None, 0.1)
    entries = log.recent(limit=3)
    assert [e.tool for e in entries] == ["tool_7", "tool_8", "tool_9"]


def test_clear_returns_count_and_empties_log():
    log = ExecutionLog()
    log.record("draw_circle", True, "drawn", 0.1)
    log.record("draw_line", True, "drawn", 0.1)
    cleared = log.clear()
    assert cleared == 2
    assert len(log) == 0
    assert log.recent() == []


def test_timestamp_is_iso_format_string():
    log = ExecutionLog()
    log.record("draw_circle", True, "drawn", 0.1)
    entry = log.recent()[0]
    assert isinstance(entry.timestamp, str)
    assert "T" in entry.timestamp
