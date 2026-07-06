import pytest

from engine.geometry.primitives import DrawingPlan
from storage.store import ProjectNotFoundError, ProjectStore


@pytest.fixture
def store(tmp_path):
    return ProjectStore(str(tmp_path / "projects"))


def test_create_project_has_one_initial_revision(store):
    plan = DrawingPlan(operations=[{"type": "circle", "center": [0, 0], "radius": 5}])
    project = store.create("demo", plan)
    assert project.name == "demo"
    assert len(project.revisions) == 1
    assert project.revisions[0].revision == 1


def test_get_round_trips_through_disk(store):
    plan = DrawingPlan(operations=[{"type": "circle", "center": [0, 0], "radius": 5}])
    created = store.create("demo", plan)
    fetched = store.get(created.id)
    assert fetched.id == created.id
    assert fetched.plan.operations[0].radius == 5.0


def test_get_unknown_project_raises(store):
    with pytest.raises(ProjectNotFoundError):
        store.get("does-not-exist")


def test_get_rejects_path_traversal(store):
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")


def test_add_revision_increments_and_updates_plan(store):
    plan_v1 = DrawingPlan(operations=[{"type": "circle", "center": [0, 0], "radius": 5}])
    project = store.create("demo", plan_v1)

    plan_v2 = DrawingPlan(
        operations=[
            {"type": "circle", "center": [0, 0], "radius": 5},
            {"type": "line", "start": [0, 0], "end": [10, 10]},
        ]
    )
    updated = store.add_revision(project.id, plan_v2, note="added a line")
    assert len(updated.revisions) == 2
    assert updated.revisions[-1].revision == 2
    assert updated.revisions[-1].note == "added a line"
    assert len(updated.plan.operations) == 2
    # the first revision is preserved, not overwritten
    assert len(updated.revisions[0].plan.operations) == 1


def test_add_revision_unknown_project_raises(store):
    plan = DrawingPlan(operations=[])
    with pytest.raises(ProjectNotFoundError):
        store.add_revision("does-not-exist", plan)


def test_list_returns_all_created_projects(store):
    store.create("a", DrawingPlan())
    store.create("b", DrawingPlan())
    names = {p.name for p in store.list()}
    assert names == {"a", "b"}


def test_list_is_empty_for_fresh_store(store):
    assert store.list() == []
