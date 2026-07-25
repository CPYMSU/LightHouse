from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lighthouse.config import Settings
from lighthouse.server import InstanceAwareApp


def test_server_health_identifies_the_exact_instance():
    app = FastAPI()

    @app.get("/other")
    def other():
        return {"ok": True}

    settings = Settings(
        database_url="postgresql://example",
        api_key="x" * 32,
        port=8793,
        instance_id="research",
        instance_name="Research",
    )
    client = TestClient(InstanceAwareApp(app, settings))

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["instance_id"] == "research"
    assert health.json()["instance_name"] == "Research"
    assert health.json()["port"] == 8793
    assert client.get("/other").json() == {"ok": True}
