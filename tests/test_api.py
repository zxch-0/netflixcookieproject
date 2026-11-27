import io
import zipfile

from conftest import NETSCAPE, wait


def test_create_and_read_job(client):
    response = client.post("/api/jobs", json={"cookies": NETSCAPE, "devices": ["pc", "tv"]})
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    payload = wait(client, job_id)
    assert payload["counters"]["valid"] == 1
    result = payload["results"][0]
    assert result["plan"] == "premium"
    assert result["nftoken"] == "TOKEN123"
    assert [link["device"] for link in result["links"]] == ["pc", "tv"]


def test_rejects_empty_input(client):
    assert client.post("/api/jobs", json={"cookies": "hello"}).status_code == 422


def test_limit_parameter(client):
    body = {"cookies": NETSCAPE + ".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tBBB222\n", "devices": ["pc"]}
    job_id = client.post("/api/jobs", json=body).json()["job_id"]
    wait(client, job_id)

    limited = client.get(f"/api/jobs/{job_id}?limit=1").json()
    assert limited["total"] == 2
    assert len(limited["results"]) == 1
    assert limited["shown"] == 1


def test_zip_upload(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("one.txt", NETSCAPE)
        archive.writestr("two.txt", ".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tZZZ999\n")

    response = client.post(
        "/api/jobs/files",
        files={"files": ("batch.zip", buffer.getvalue(), "application/zip")},
        data={"devices": "pc"},
    )
    assert response.status_code == 202
    payload = wait(client, response.json()["job_id"])
    assert payload["total"] == 2


def test_reports(client):
    job_id = client.post("/api/jobs", json={"cookies": NETSCAPE}).json()["job_id"]
    wait(client, job_id)

    text = client.get(f"/api/jobs/{job_id}/report.txt")
    assert text.status_code == 200
    assert "PREMIUM" in text.text
    assert "TOKEN123" in text.text

    data = client.get(f"/api/jobs/{job_id}/report.json").json()
    assert data["results"][0]["cookies"].startswith(".netflix.com")


def test_cancel_unknown_job(client):
    assert client.delete("/api/jobs/nope").status_code == 404


def test_auth_required(client, monkeypatch):
    from dataclasses import replace

    from app import main

    monkeypatch.setattr(main, "SETTINGS", replace(main.SETTINGS, token="secret"))
    assert client.post("/api/jobs", json={"cookies": NETSCAPE}).status_code == 401
    assert client.post("/api/jobs", json={"cookies": NETSCAPE}, headers={"X-Access-Token": "secret"}).status_code == 202
