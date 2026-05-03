"""Smoke tests for Phase 0 — confirm app imports and healthz route exists."""

from __future__ import annotations

from fastapi.testclient import TestClient

from scryer.server.app import create_app


def test_app_creates() -> None:
    app = create_app()
    assert app.title == "scryer"


def test_healthz_route_registered() -> None:
    app = create_app()
    routes = [r.path for r in app.routes]
    assert "/api/v1/healthz" in routes


def test_healthz_returns_response() -> None:
    """DB ping will fail (no DB in CI without a service); endpoint should still respond."""
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "0.0.0"
    assert body["status"] in {"ok", "degraded"}
    assert isinstance(body["db_ok"], bool)


def test_healthz_deep_route_registered() -> None:
    app = create_app()
    routes = [r.path for r in app.routes]
    assert "/api/v1/healthz/deep" in routes


def test_healthz_deep_returns_check_shape() -> None:
    """All sub-checks present; 200 if everything ok, 503 if any degraded."""
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/v1/healthz/deep")
    body = response.json()
    assert response.status_code in (200, 503)
    for key in ("db", "migration", "r2", "webhook_queue_depth", "stale_runs"):
        assert key in body
        assert isinstance(body[key]["ok"], bool)
