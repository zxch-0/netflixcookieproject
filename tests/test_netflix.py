from dataclasses import replace

import pytest

from app import main

VALID_PAGE = '{"currentCountry":"FR","membershipStatus":"current_member","localizedPlanName":"Premium","maxStreams":4,"videoQuality":"UHD"}'
LOGIN_PAGE = '<html><body><form id="login-form">Sign In</form></body></html>'


class FakeCookies(dict):
    def clear(self):
        super().clear()


class FakeResponse:
    def __init__(self, status=200, text="", location=None):
        self.status_code = status
        self.text = text
        self.headers = {"Location": location} if location else {}


class FakeSession:
    def __init__(self, responses):
        self.cookies = FakeCookies()
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def run(monkeypatch, responses, retries=1):
    session = FakeSession(responses)
    monkeypatch.setattr(main, "http_session", lambda *args, **kwargs: session)
    monkeypatch.setattr(main, "SETTINGS", replace(main.SETTINGS, retries=retries, timeout=3))
    return main.check_account({"NetflixId": "AAA"}, [])


def test_valid_account(monkeypatch):
    outcome = run(monkeypatch, [FakeResponse(200, VALID_PAGE)])
    assert outcome.status == "ok"
    assert outcome.info["plan_name"] == "Premium"
    assert main.is_subscribed(outcome.info) is True


def test_login_redirect_is_invalid(monkeypatch):
    outcome = run(monkeypatch, [FakeResponse(302, "", "https://www.netflix.com/login?next=/account")])
    assert outcome.status == "invalid"
    assert "login" in outcome.error


def test_login_page_is_invalid(monkeypatch):
    outcome = run(monkeypatch, [FakeResponse(200, LOGIN_PAGE)])
    assert outcome.status == "invalid"


def test_unauthorized_is_invalid(monkeypatch):
    outcome = run(monkeypatch, [FakeResponse(401, "")])
    assert outcome.status == "invalid"


def test_forbidden_is_error_not_invalid(monkeypatch):
    outcome = run(monkeypatch, [FakeResponse(403, ""), FakeResponse(403, "")], retries=2)
    assert outcome.status == "error"
    assert outcome.error == "HTTP 403"


def test_empty_page_is_error(monkeypatch):
    outcome = run(monkeypatch, [FakeResponse(200, "<html>nothing</html>")], retries=2)
    assert outcome.status == "error"


def test_timeout_is_error(monkeypatch):
    import requests

    class Boom(FakeSession):
        def get(self, url, **kwargs):
            raise requests.exceptions.Timeout()

    monkeypatch.setattr(main, "http_session", lambda *args, **kwargs: Boom([]))
    monkeypatch.setattr(main, "SETTINGS", replace(main.SETTINGS, retries=1, timeout=3))
    assert main.check_account({"NetflixId": "AAA"}, []).status == "error"


def test_extract_info_reads_fields():
    info = main.extract_info(VALID_PAGE)
    assert info["country"] == "FR"
    assert info["streams"] == "4"
    assert info["quality"] == "UHD"
