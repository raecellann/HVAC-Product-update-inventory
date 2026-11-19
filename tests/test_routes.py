"""Route smoke tests: JSON endpoints, upload rejections, DISABLE_APPLY."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(sample_cache):
    import importlib
    import webapp.app as app_mod
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200


def test_dashboard_loads_without_key(client):
    """Auth was removed — the API is now open on localhost."""
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == {"runs": []}


def test_api_responses_are_no_store(client):
    r = client.get("/api/runs")
    assert r.headers.get("cache-control") == "no-store"


def test_upload_rejects_xls(client):
    files = {"file": ("book.xls", io.BytesIO(b"PK\x03\x04"), "application/vnd.ms-excel")}
    r = client.post("/api/upload", files=files)
    assert r.status_code == 400
    assert ".xls" in r.json()["detail"]


def test_upload_rejects_non_excel(client):
    files = {"file": ("book.txt", io.BytesIO(b"hello"), "text/plain")}
    r = client.post("/api/upload", files=files)
    assert r.status_code == 400


def test_list_runs_empty(client):
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == {"runs": []}


def test_get_run_404(client):
    r = client.get("/api/run/99999")
    assert r.status_code == 404


def test_apply_blocked_in_dev(client, monkeypatch):
    """When DISABLE_APPLY=true, /api/apply/{id} must refuse even on a valid preview."""
    monkeypatch.setenv("DISABLE_APPLY", "true")

    from webapp import history, services
    rid = history.create_run("preview", file_name="x.xlsx")
    d = services.run_dir(rid)
    (d / "preview.json").write_text("[]", encoding="utf-8")
    history.record_counts(rid, {"matched": 0, "will_change": 0, "excel_rows": 0}, [])
    history.set_status(rid, "succeeded")

    r = client.post(f"/api/apply/{rid}")
    assert r.status_code == 403
    assert "dev mode" in r.json()["detail"].lower()


def test_dev_flags_endpoint(client, monkeypatch):
    monkeypatch.setenv("DISABLE_APPLY", "true")
    r = client.get("/api/dev/flags")
    assert r.status_code == 200
    assert r.json() == {"disable_apply": True}
