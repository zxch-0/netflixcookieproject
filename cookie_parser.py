"""Parsing of Netflix cookie pastes into checkable accounts.

Accepted inputs:
  * Netscape cookie file format (tab or whitespace separated)
  * JSON: list of {name, value} objects, {"cookies": [...]} or a flat mapping
  * Raw header style: ``NetflixId=...; SecureNetflixId=...``

Several accounts can be pasted at once. Blocks are split on separators
(``---``, ``###``, ``===``), blank lines, or repeated ``NetflixId=`` pairs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

REQUIRED_COOKIES = ("NetflixId", "SecureNetflixId")
OPTIONAL_IMPORTANT = ("nfvdid", "OptanonConsent", "flwssn")

MAX_ACCOUNTS = 500
MAX_COOKIES_PER_ACCOUNT = 80
MAX_COOKIE_VALUE_LENGTH = 4096

_SEPARATOR_RE = re.compile(r"(?m)^[ \t]*(?:-{3,}|#{3,}|={3,}|\*{3,})[ \t]*$")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")
_INLINE_SPLIT_RE = re.compile(r"(?i)(?<=[;\n])[ \t]*(?=NetflixId[ \t]*[=:])")
_COOKIE_PAIR_RE = re.compile(r"""([A-Za-z0-9_\-]{1,64})[ \t]*[=:][ \t]*["']?([^;"'\s,]{1,4096})""")
_NETSCAPE_LINE_RE = re.compile(
    r"^(?P<domain>\S+)[ \t]+(?P<flag>TRUE|FALSE)[ \t]+(?P<path>\S+)[ \t]+"
    r"(?P<secure>TRUE|FALSE)[ \t]+(?P<expiry>-?\d+)[ \t]+(?P<name>[^\s]+)[ \t]+(?P<value>.*)$"
)
_HEADER_PREFIX_RE = re.compile(r"(?i)^\s*cookie\s*:\s*")


class ParseError(ValueError):
    """Raised when a paste cannot be interpreted at all."""


@dataclass
class Account:
    """A single account extracted from a paste."""

    index: int
    cookies: dict[str, str] = field(default_factory=dict)
    label: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"Compte {self.index + 1}"

    @property
    def cookie_count(self) -> int:
        return len(self.cookies)

    @property
    def has_required(self) -> bool:
        return all(bool(self.cookies.get(name)) for name in REQUIRED_COOKIES)

    def public(self) -> dict[str, Any]:
        """Serialisable representation sent to the browser."""
        return {
            "id": self.index,
            "label": self.label,
            "cookie_count": self.cookie_count,
            "cookies": dict(self.cookies),
        }

    def header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.cookies.items())

    @classmethod
    def from_public(cls, payload: dict[str, Any], index: int) -> "Account":
        cookies = payload.get("cookies") or {}
        if not isinstance(cookies, dict):
            raise ParseError("Champ 'cookies' invalide")
        cleaned = {
            str(name): str(value)
            for name, value in cookies.items()
            if str(name).strip() and str(value).strip()
        }
        if not cleaned:
            raise ParseError(f"Le compte {index + 1} ne contient aucun cookie")
        if len(cleaned) > MAX_COOKIES_PER_ACCOUNT:
            cleaned = dict(list(cleaned.items())[:MAX_COOKIES_PER_ACCOUNT])
        label = str(payload.get("label") or f"Compte {index + 1}")
        return cls(index=index, cookies=cleaned, label=label)


def _clean(value: str) -> str:
    return value.strip().strip('"').strip("'").strip()


def _store(cookies: dict[str, str], name: str, value: str) -> None:
    name = _clean(name)
    value = _clean(value)
    if not name or not value:
        return
    if len(value) > MAX_COOKIE_VALUE_LENGTH:
        value = value[:MAX_COOKIE_VALUE_LENGTH]
    if len(cookies) >= MAX_COOKIES_PER_ACCOUNT:
        return
    cookies[name] = value


def _from_json(block: str) -> dict[str, str] | None:
    text = block.strip()
    candidates: list[str] = [text]
    start = min((pos for pos in (text.find("["), text.find("{")) if pos != -1), default=-1)
    if start > 0:
        candidates.append(text[start:])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        cookies: dict[str, str] = {}
        if isinstance(data, dict):
            inner = data.get("cookies")
            if isinstance(inner, list):
                _collect_list(inner, cookies)
            elif isinstance(inner, dict):
                for name, value in inner.items():
                    _store(cookies, str(name), str(value))
            for name, value in data.items():
                if isinstance(value, (str, int, float)) and name not in ("cookies",):
                    _store(cookies, str(name), str(value))
        elif isinstance(data, list):
            _collect_list(data, cookies)
        if cookies:
            return cookies
    return None


def _collect_list(items: list[Any], cookies: dict[str, str]) -> None:
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or item.get("key")
            value = item.get("value")
            if name is not None and value is not None:
                _store(cookies, str(name), str(value))
        elif isinstance(item, str) and "=" in item:
            name, _, value = item.partition("=")
            _store(cookies, name, value)


def _from_netscape(block: str) -> dict[str, str] | None:
    cookies: dict[str, str] = {}
    matched = False
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") and not stripped.startswith("#HttpOnly_"):
            continue
        if stripped.startswith("#HttpOnly_"):
            stripped = stripped[len("#HttpOnly_"):]
        match = _NETSCAPE_LINE_RE.match(stripped)
        if match:
            matched = True
            _store(cookies, match.group("name"), match.group("value"))
            continue
        parts = re.split(r"\t+", stripped)
        if len(parts) >= 7:
            matched = True
            _store(cookies, parts[5], parts[6])
        elif len(parts) >= 2 and "=" not in parts[0] and "=" not in parts[1]:
            matched = True
            _store(cookies, parts[0], parts[1])
    return cookies if matched and cookies else None


def _from_raw(block: str) -> dict[str, str] | None:
    text = _HEADER_PREFIX_RE.sub("", block)
    cookies: dict[str, str] = {}
    for match in _COOKIE_PAIR_RE.finditer(text):
        _store(cookies, match.group(1), match.group(2))
    return cookies if cookies else None


def _cookies_from_block(block: str) -> dict[str, str]:
    if not block.strip():
        return {}
    for parser in (_from_json, _from_netscape, _from_raw):
        try:
            cookies = parser(block)
        except (ValueError, TypeError, KeyError):
            cookies = None
        if cookies:
            return cookies
    return {}


def _split_blocks(text: str) -> list[str]:
    parts = [part for part in _SEPARATOR_RE.split(text) if part.strip()]
    blocks: list[str] = []
    for part in parts:
        for chunk in _BLANK_LINE_RE.split(part):
            if chunk.strip():
                blocks.append(chunk)
    if len(blocks) == 1:
        blocks = [chunk for chunk in _INLINE_SPLIT_RE.split(blocks[0]) if chunk.strip()]
    return blocks


def parse_accounts(text: str) -> list[Account]:
    """Parse a paste into deduplicated accounts holding at least one NetflixId."""
    if not text or not text.strip():
        raise ParseError("Le texte est vide")

    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    accounts: list[Account] = []
    seen: set[tuple[str, str]] = set()

    for block in _split_blocks(normalised):
        cookies = _cookies_from_block(block)
        if not cookies:
            continue
        if not any(name in cookies for name in REQUIRED_COOKIES):
            continue
        key = (cookies.get("NetflixId", ""), cookies.get("SecureNetflixId", ""))
        if key in seen:
            continue
        seen.add(key)
        accounts.append(Account(index=len(accounts), cookies=cookies, source=block.strip()[:200]))
        if len(accounts) >= MAX_ACCOUNTS:
            break

    return accounts
