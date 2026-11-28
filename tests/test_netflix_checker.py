import pytest

import netflix_checker
from cookie_parser import parse_accounts


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.encoding = "utf-8"


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.cookies = type("C", (), {"clear": lambda self: None, "set": lambda *a, **k: None})()
        self.headers = {}

    def get(self, url, allow_redirects=False, timeout=None):
        return self.responses.pop(0)


def run_check(monkeypatch, responses):
    monkeypatch.setattr(netflix_checker, "_session", lambda: FakeSession(responses))
    account = parse_accounts("NetflixId=a; SecureNetflixId=b")[0]
    return netflix_checker.check_one(account)


def test_valid_account(monkeypatch):
    body = '<html>"planName":"Premium","countryOfSignup":"FR","profileName":"Alice"</html>' + "x" * 600
    result = run_check(monkeypatch, [FakeResponse(200, body)])
    assert result["valid"] is True
    assert result["plan"] == "Premium"
    assert result["country"] == "FR"
    assert result["profiles"] == ["Alice"]


def test_login_redirect_is_invalid(monkeypatch):
    result = run_check(monkeypatch, [FakeResponse(302, "", {"location": "/login?next=/account"})])
    assert result["valid"] is False
    assert result["status"] == "invalid"


def test_login_form_is_invalid(monkeypatch):
    body = '<div data-uia="login-form"></div>' + "x" * 600
    result = run_check(monkeypatch, [FakeResponse(200, body)])
    assert result["status"] == "invalid"


def test_server_error_is_error(monkeypatch):
    result = run_check(monkeypatch, [FakeResponse(503, "nope")])
    assert result["status"] == "error"
    assert result["valid"] is False


def test_check_many_keeps_order(monkeypatch):
    body = '"planName":"Standard"' + "x" * 600

    def fake_check_one(account):
        return {"id": account.index, "valid": True}

    monkeypatch.setattr(netflix_checker, "check_one", fake_check_one)
    accounts = parse_accounts("NetflixId=a; SecureNetflixId=b\n---\nNetflixId=c; SecureNetflixId=d")
    results = netflix_checker.check_many(accounts)
    assert [result["id"] for result in results] == [0, 1]


def test_check_many_empty():
    assert netflix_checker.check_many([]) == []
