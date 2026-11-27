import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main  # noqa: E402

NETSCAPE = ".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tAAA111\n"


@pytest.fixture(autouse=True)
def clean_jobs():
    main.JOBS.clear()
    main.HITS.clear()
    yield
    main.JOBS.clear()
    main.HITS.clear()


@pytest.fixture
def client(monkeypatch):
    def fake_check(cookies, pool):
        return main.Outcome(
            "ok",
            info={
                "country": "FR",
                "membership_status": "current_member",
                "plan_name": "Premium",
                "streams": "4",
                "quality": "UHD",
                "email": "user@example.com",
            },
        )

    monkeypatch.setattr(main, "check_account", fake_check)
    monkeypatch.setattr(main, "create_nftoken", lambda *args, **kwargs: ("TOKEN123", "2030-01-01 00:00:00 UTC"))
    return TestClient(main.app)


def wait(client, job_id, timeout=5.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in {"done", "cancelled", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("job did not finish")
