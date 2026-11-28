import pytest
from fastapi.testclient import TestClient

import app as app_module
import netflix_checker


@pytest.fixture()
def client():
    return TestClient(app_module.app)


@pytest.fixture(autouse=True)
def fake_checker(monkeypatch):
    def fake_check_many(accounts):
        return [
            {
                "id": account.index,
                "label": account.label,
                "valid": True,
                "status": "valid",
                "plan": "Premium",
                "country": "FR",
                "email": "a@b.c",
                "member_since": "",
                "profiles": ["Alice"],
                "profiles_count": 1,
                "http_status": 200,
                "final_path": "/account",
                "elapsed_ms": 12,
                "error": "",
                "cookies": dict(account.cookies),
            }
            for account in accounts
        ]

    monkeypatch.setattr(netflix_checker, "check_many", fake_check_many)


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_index_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Netflix Cookie Checker" in response.text


def test_parse_then_check(client):
    parsed = client.post("/api/parse", json={"text": "NetflixId=a; SecureNetflixId=b"})
    assert parsed.status_code == 200
    accounts = parsed.json()["accounts"]
    assert parsed.json()["count"] == 1

    checked = client.post("/api/check", json={"accounts": accounts})
    assert checked.status_code == 200
    payload = checked.json()
    assert payload["results"][0]["valid"] is True
    assert payload["summary"]["valid"] == 1


def test_parse_empty(client):
    assert client.post("/api/parse", json={"text": ""}).status_code == 400


def test_parse_without_netflix_id(client):
    assert client.post("/api/parse", json={"text": "Foo=bar"}).status_code == 400


def test_check_without_accounts(client):
    assert client.post("/api/check", json={"accounts": []}).status_code == 400


def test_check_batch_limit(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_BATCH", 2)
    accounts = [
        {"id": index, "label": f"Compte {index}", "cookies": {"NetflixId": str(index)}}
        for index in range(3)
    ]
    assert client.post("/api/check", json={"accounts": accounts}).status_code == 400


def test_access_token_required(client, monkeypatch):
    monkeypatch.setattr(app_module, "ACCESS_TOKEN", "secret")
    assert client.get("/api/config").status_code == 401
    ok = client.get("/api/config", headers={"X-Access-Token": "secret"})
    assert ok.status_code == 200
    assert ok.json()["auth_required"] is True


def test_check_text_endpoint(client):
    response = client.post("/api/check-text", json={"text": "NetflixId=a; SecureNetflixId=b"})
    assert response.status_code == 200
    assert response.json()["results"][0]["plan"] == "Premium"
