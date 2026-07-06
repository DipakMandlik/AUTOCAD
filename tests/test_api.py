import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.context import ServerContext
from cad.registry import get_backend
from engine.planner.planner import Planner
from engine.validator.engine import ValidationEngine
from nlp.fallback import FallbackParser


@pytest.fixture
def client(tmp_path):
    ctx = ServerContext(
        planner=Planner(),
        validator=ValidationEngine(),
        backend=get_backend("dxf", output_dir=str(tmp_path)),
        color_parser=FallbackParser(),
    )
    return TestClient(create_app(ctx))


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["backend"] == "dxf"


def test_dashboard_is_served(client):
    index = client.get("/dashboard/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]

    script = client.get("/dashboard/app.js")
    assert script.status_code == 200

    style = client.get("/dashboard/styles.css")
    assert style.status_code == 200


def test_list_tools_matches_registry(client):
    response = client.get("/tools")
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()}
    assert "draw_line" in names
    assert "process_command" in names


def test_call_tool_draws_entity(client):
    response = client.post("/tools/draw_circle", json={"center": [0, 0], "radius": 10})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["handle"]


def test_call_unknown_tool_returns_404(client):
    response = client.post("/tools/does_not_exist", json={})
    assert response.status_code == 404


def test_call_tool_with_invalid_geometry_returns_200_with_failure(client):
    # a bad drawing request is a normal, well-formed API response, not a
    # server error — the HTTP layer should not conflate the two.
    response = client.post("/tools/draw_circle", json={"center": [0, 0], "radius": -5})
    assert response.status_code == 200
    assert response.json()["success"] is False


def test_process_command_via_rest(client):
    response = client.post("/tools/process_command", json={"command": "draw a line from (0,0) to (10,10)"})
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_validate_endpoint_never_touches_backend(client):
    response = client.post(
        "/drawings/validate",
        json={"operations": [{"type": "line", "start": [0, 0], "end": [0, 0]}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_valid"] is False
    assert any(issue["code"] == "zero_length_line" for issue in body["issues"])


def test_execute_endpoint_draws_multiple_entities(client):
    response = client.post(
        "/drawings/execute",
        json={
            "operations": [
                {"type": "circle", "center": [0, 0], "radius": 5},
                {"type": "line", "start": [0, 0], "end": [10, 10]},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(body["results"]) == 2
    assert all(r["success"] for r in body["results"])
    assert body["results"][0]["entity"]["type"] == "circle"
    assert body["results"][1]["entity"]["type"] == "line"


def test_execute_endpoint_autofixes_and_reports(client):
    response = client.post(
        "/drawings/execute",
        json={
            "operations": [
                {"type": "circle", "center": [0, 0], "radius": 5},
                {"type": "circle", "center": [0, 0], "radius": 5},
            ]
        },
    )
    body = response.json()
    assert body["success"] is True
    assert len(body["results"]) == 1  # the duplicate was dropped
    assert any(f["code"] == "duplicate_entity" for f in body["autofixed"])


def test_execute_endpoint_reports_uncorrectable_validation_failure(client):
    response = client.post(
        "/drawings/execute",
        json={
            "operations": [
                {"type": "arc", "center": [0, 0], "radius": 5, "start_angle": 10, "end_angle": 10}
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "issues" in body
