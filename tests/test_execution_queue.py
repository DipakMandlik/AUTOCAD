from apps.execution_queue import ExecutionQueue


def test_enqueue_returns_item_with_queued_status():
    queue = ExecutionQueue()
    item = queue.enqueue("draw_circle", {"center": [0, 0], "radius": 5})
    assert item.status == "queued"
    assert item.result is None
    assert item.tool == "draw_circle"
    assert item.arguments == {"center": [0, 0], "radius": 5}


def test_ids_are_monotonic_and_never_reused():
    queue = ExecutionQueue()
    ids = [queue.enqueue("draw_circle", {}).id for _ in range(3)]
    assert ids == [1, 2, 3]
    queue.remove(2)
    new_item = queue.enqueue("draw_line", {})
    assert new_item.id == 4


def test_items_preserves_enqueue_order():
    queue = ExecutionQueue()
    queue.enqueue("draw_circle", {})
    queue.enqueue("draw_line", {})
    assert [i.tool for i in queue.items()] == ["draw_circle", "draw_line"]


def test_get_returns_none_for_unknown_id():
    queue = ExecutionQueue()
    assert queue.get(999) is None


def test_get_returns_matching_item():
    queue = ExecutionQueue()
    item = queue.enqueue("draw_circle", {})
    assert queue.get(item.id) is item


def test_remove_returns_false_for_unknown_id():
    queue = ExecutionQueue()
    assert queue.remove(999) is False


def test_remove_deletes_only_matching_item():
    queue = ExecutionQueue()
    first = queue.enqueue("draw_circle", {})
    second = queue.enqueue("draw_line", {})
    assert queue.remove(first.id) is True
    assert [i.id for i in queue.items()] == [second.id]


def test_clear_returns_count_and_empties_queue():
    queue = ExecutionQueue()
    queue.enqueue("draw_circle", {})
    queue.enqueue("draw_line", {})
    assert queue.clear() == 2
    assert len(queue) == 0
    assert queue.items() == []
