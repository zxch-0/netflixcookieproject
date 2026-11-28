from dataclasses import replace

from fastapi import FastAPI

import app as package
from app import main
from conftest import NETSCAPE, wait


def test_package_exports_asgi_app():
    """`uvicorn app:app` doit trouver une application, pas une docstring."""
    assert isinstance(package.app, FastAPI)
    assert package.app is main.app


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_index_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Netflix Dispo Checker" in response.text


def test_static_assets_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_job_runs_to_completion(client):
    response = client.post("/api/jobs", json={"cookies": NETSCAPE, "devices": ["pc"]})
    assert response.status_code == 202
    payload = wait(client, response.json()["job_id"])
    assert payload["total"] == 1
    assert payload["results"][0]["status"] == "valid"
    assert payload["results"][0]["plan"] == "premium"


def test_concurrency_is_clamped(client):
    response = client.post("/api/jobs", json={"cookies": NETSCAPE, "concurrency": 999})
    assert response.status_code == 202
    assert response.json()["concurrency"] == 1


def test_config_lists_plans(client):
    payload = client.get("/api/config").json()
    assert payload["auth_required"] is False
    assert any(plan["key"] == "premium" for plan in payload["plans"])


def test_access_token_required(client, monkeypatch):
    monkeypatch.setattr(main, "SETTINGS", replace(main.SETTINGS, token="secret"))
    assert client.get("/api/jobs/unknown").status_code == 401
    assert client.get("/api/jobs/unknown", headers={"X-Access-Token": "secret"}).status_code == 404


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404
