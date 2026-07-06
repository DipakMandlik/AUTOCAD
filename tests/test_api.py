import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.context import ServerContext
from cad.registry import get_backend
from engine.planner.planner import Planner
from engine.validator.engine import ValidationEngine
from nlp.fallback import FallbackParser
from storage.store import ProjectStore


@pytest.fixture
def client(tmp_path):
    ctx = ServerContext(
        planner=Planner(),
        validator=ValidationEngine(),
        backend=get_backend("dxf", output_dir=str(tmp_path / "output")),
        color_parser=FallbackParser(),
        project_store=ProjectStore(str(tmp_path / "projects")),
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


def test_current_drawing_and_clear(client):
    client.post("/tools/draw_circle", json={"center": [0, 0], "radius": 5})
    current = client.get("/drawings/current").json()
    assert len(current["operations"]) == 1

    cleared = client.post("/drawings/clear").json()
    assert cleared["success"] is True
    assert client.get("/drawings/current").json()["operations"] == []


def test_project_lifecycle_via_rest(client):
    client.post("/tools/draw_circle", json={"center": [0, 0], "radius": 5})

    created = client.post("/projects", json={"name": "demo"}).json()
    assert created["success"] is True
    project_id = created["project_id"]

    client.post("/tools/draw_line", json={"start": [0, 0], "end": [10, 10]})
    snapshot = client.post(f"/projects/{project_id}/revisions", json={"note": "added a line"}).json()
    assert snapshot["revision"] == 2

    fetched = client.get(f"/projects/{project_id}").json()
    assert fetched["project"]["name"] == "demo"
    assert len(fetched["project"]["revisions"]) == 2

    listed = client.get("/projects").json()
    assert any(p["id"] == project_id for p in listed["projects"])

    client.post("/drawings/clear")
    loaded = client.post(f"/projects/{project_id}/load").json()
    assert loaded["success"] is True
    assert len(loaded["results"]) == 2


def test_get_unknown_project_returns_404(client):
    response = client.get("/projects/does-not-exist")
    assert response.status_code == 404


def test_load_unknown_project_returns_200_with_failure(client):
    # unlike GET, this dispatches through the generic tool-handler shape,
    # which reports failure in the body rather than as an HTTP error
    response = client.post("/projects/does-not-exist/load")
    assert response.status_code == 200
    assert response.json()["success"] is False


def test_render_current_drawing_svg(client):
    client.post("/tools/draw_circle", json={"center": [0, 0], "radius": 5})
    response = client.get("/drawings/current/render")  # default format=svg
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert "<svg" in response.text


def test_render_current_drawing_png(client):
    client.post("/tools/draw_circle", json={"center": [0, 0], "radius": 5})
    response = client.get("/drawings/current/render?format=png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_render_rejects_unknown_format(client):
    response = client.get("/drawings/current/render?format=bogus")
    assert response.status_code == 422


def test_render_empty_drawing_does_not_error(client):
    response = client.get("/drawings/current/render")
    assert response.status_code == 200
    assert "<svg" in response.text


def test_render_project(client):
    client.post("/tools/draw_circle", json={"center": [0, 0], "radius": 5})
    created = client.post("/projects", json={"name": "demo"}).json()
    response = client.get(f"/projects/{created['project_id']}/render")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"


def test_render_unknown_project_returns_404(client):
    response = client.get("/projects/does-not-exist/render")
    assert response.status_code == 404


def test_export_current_drawing_scr(client):
    client.post("/tools/draw_circle", json={"center": [0, 0], "radius": 5})
    response = client.get("/drawings/current/export")  # default format=scr
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="drawing.scr"'
    assert "CIRCLE" in response.text


def test_export_current_drawing_lisp(client):
    client.post("/tools/draw_circle", json={"center": [0, 0], "radius": 5})
    response = client.get("/drawings/current/export?format=lisp")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="drawing.lsp"'
    assert '(command "CIRCLE"' in response.text


def test_export_rejects_unknown_format(client):
    response = client.get("/drawings/current/export?format=bogus")
    assert response.status_code == 422


def test_export_project(client):
    client.post("/tools/draw_circle", json={"center": [0, 0], "radius": 5})
    created = client.post("/projects", json={"name": "demo"}).json()
    response = client.get(f"/projects/{created['project_id']}/export?format=lisp")
    assert response.status_code == 200
    assert "CIRCLE" in response.text


def test_export_unknown_project_returns_404(client):
    response = client.get("/projects/does-not-exist/export")
    assert response.status_code == 404


def test_list_symbols(client):
    response = client.get("/symbols")
    assert response.status_code == 200
    names = {s["name"] for s in response.json()["symbols"]}
    assert {"resistor", "gate_valve", "door_swing"} <= names


def test_preview_symbol_svg(client):
    response = client.get("/symbols/resistor/preview")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert "<svg" in response.text


def test_preview_symbol_png(client):
    response = client.get("/symbols/resistor/preview?format=png&scale=2&rotation=45")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_preview_unknown_symbol_returns_404(client):
    response = client.get("/symbols/not-a-symbol/preview")
    assert response.status_code == 404


def test_preview_rejects_non_positive_scale(client):
    response = client.get("/symbols/resistor/preview?scale=0")
    assert response.status_code == 422


def test_insert_symbol_via_rest(client):
    response = client.post(
        "/tools/insert_symbol", json={"symbol_name": "north_arrow", "position": [0, 0], "rotation": 30}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(body["results"]) == 2


def test_import_svg_via_rest(client):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<circle cx="5" cy="5" r="2"/><rect x="1" y="1" width="2" height="2"/>'
        "</svg>"
    )
    response = client.post("/tools/import_svg", json={"svg_content": svg})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(body["results"]) == 2


def test_import_svg_via_rest_rejects_bad_xml(client):
    response = client.post("/tools/import_svg", json={"svg_content": "<svg><line x1=0/></svg>"})
    assert response.status_code == 200
    assert response.json()["success"] is False
