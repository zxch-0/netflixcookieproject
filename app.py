"""FastAPI application exposing the Netflix cookie checker."""

from __future__ import annotations

import os
import time
from typing import Any

import uvicorn
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import netflix_checker
from cookie_parser import MAX_ACCOUNTS, Account, ParseError, parse_accounts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "").strip()
MAX_BATCH = max(1, int(os.environ.get("MAX_ACCOUNTS_PER_REQUEST", "25")))
MAX_PASTE_CHARS = int(os.environ.get("MAX_PASTE_CHARS", "2000000"))
APP_VERSION = os.environ.get("GIT_COMMIT", "dev")

app = FastAPI(title="Netflix Cookie Checker", version="2.0.0", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def require_token(request: Request, token: str | None = Query(default=None)) -> None:
    if not ACCESS_TOKEN:
        return
    supplied = (request.headers.get("x-access-token") or token or "").strip()
    if supplied != ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Jeton d'accès invalide")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    response.headers["X-Response-Time-ms"] = str(int((time.perf_counter() - started) * 1000))
    return response


@app.exception_handler(ParseError)
async def parse_error_handler(request: Request, exc: ParseError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/version")
def version() -> dict[str, Any]:
    return {
        "version": app.version,
        "commit": APP_VERSION,
        "max_batch": MAX_BATCH,
        "max_accounts": MAX_ACCOUNTS,
        "workers": netflix_checker.MAX_WORKERS,
        "auth_required": bool(ACCESS_TOKEN),
    }


@app.get("/api/config", dependencies=[Depends(require_token)])
def config() -> dict[str, Any]:
    return {
        "auth_required": bool(ACCESS_TOKEN),
        "max_batch": MAX_BATCH,
        "max_accounts": MAX_ACCOUNTS,
        "max_paste_chars": MAX_PASTE_CHARS,
    }


@app.post("/api/parse", dependencies=[Depends(require_token)])
def parse(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="Aucun cookie fourni")
    if len(text) > MAX_PASTE_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Texte trop volumineux (max {MAX_PASTE_CHARS} caractères)",
        )

    accounts = parse_accounts(text)
    if not accounts:
        raise HTTPException(
            status_code=400,
            detail="Aucun cookie NetflixId exploitable trouvé dans le texte",
        )
    truncated = len(accounts) > MAX_ACCOUNTS
    accounts = accounts[:MAX_ACCOUNTS]
    return {
        "accounts": [account.public() for account in accounts],
        "count": len(accounts),
        "truncated": truncated,
    }


@app.post("/api/check", dependencies=[Depends(require_token)])
def check(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    raw_accounts = payload.get("accounts")
    if not isinstance(raw_accounts, list) or not raw_accounts:
        raise HTTPException(status_code=400, detail="Aucun compte à vérifier")
    if len(raw_accounts) > MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Trop de comptes dans un lot (max {MAX_BATCH})",
        )

    accounts: list[Account] = []
    for position, raw in enumerate(raw_accounts):
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail=f"Compte {position + 1} invalide")
        try:
            accounts.append(Account.from_public(raw, position))
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    results = netflix_checker.check_many(accounts)
    valid = sum(1 for result in results if result["valid"])
    return {
        "results": results,
        "summary": {
            "checked": len(results),
            "valid": valid,
            "invalid": sum(1 for result in results if result["status"] == "invalid"),
            "errors": sum(1 for result in results if result["status"] == "error"),
        },
    }


@app.post("/api/check-text", dependencies=[Depends(require_token)])
def check_text(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Convenience endpoint: parse and check a paste in a single call."""
    parsed = parse(payload)
    accounts = parsed["accounts"][:MAX_BATCH]
    results = netflix_checker.check_many([Account.from_public(raw, i) for i, raw in enumerate(accounts)])
    return {
        "count": len(accounts),
        "remaining": max(0, parsed["count"] - len(accounts)),
        "results": results,
    }


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
