"""The React dashboard build is served under /app with an index.html fallback."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve.web import app as app_module


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "index.html").write_text("<div id=root></div>")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", tmp_path)
    return TestClient(app_module.create_app())


def test_client_routes_fall_back_to_index(client: TestClient):
    for path in ("/app", "/app/", "/app/hauling", "/app/market-making"):
        r = client.get(path)
        assert r.status_code == 200
        assert "id=root" in r.text


def test_assets_served_and_missing_files_404(client: TestClient):
    assert client.get("/app/assets/app.js").status_code == 200
    assert client.get("/app/nope.js").status_code == 404
