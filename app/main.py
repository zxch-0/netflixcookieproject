"""FastAPI entrypoint."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import DEVICES, PLAN_LABELS, PLAN_RANK, SETTINGS, parse_cookies, parse_proxy_text, pick_devices
from .jobs import build_report, new_job, start, store

BASE_DIR = Path(__file__).resolve().parent
INDEX = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

app = FastAPI(title="Netflix Dispo Checker", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_hits: dict[str, list[float]] = {}


def require_token(x_access_token: str | None = Header(default=None)) -> None:
    if SETTINGS.token and x_access_token != SETTINGS.token:
        raise HTTPException(status_code=401, detail="Invalid or missing access token")


class JobRequest(BaseModel):
    cookies: str = Field(min_length=1, max_length=4_000_000)
    devices: list[str] = Field(default_factory=lambda: ["pc"])
    concurrency: int | None = None
    proxies: str | None = None


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
        "max_cookies_per_job": SETTINGS.max_cookies,
        "server_proxies": len(SETTINGS.proxies),
        "nftoken_enabled": SETTINGS.nftoken,
    }


@app.post("/api/jobs", dependencies=[Depends(require_token)])
async def create_job(payload: JobRequest, request: Request) -> JSONResponse:
    store.purge()
    if store.active() >= SETTINGS.max_jobs:
        raise HTTPException(status_code=429, detail="Too many jobs running, retry shortly")

    client = request.client.host if request.client else "unknown"
    now = asyncio.get_event_loop().time()
    recent = [stamp for stamp in _hits.get(client, []) if now - stamp < 60]
    if len(recent) >= 20:
        raise HTTPException(status_code=429, detail="Rate limit: 20 jobs per minute")
    _hits[client] = recent + [now]

    accounts = parse_cookies(payload.cookies)
    if not accounts:
        raise HTTPException(status_code=422, detail="No Netflix cookie found in the pasted text")

    skipped = max(0, len(accounts) - SETTINGS.max_cookies)
    accounts = accounts[: SETTINGS.max_cookies]
    concurrency = max(1, min(payload.concurrency or SETTINGS.concurrency, SETTINGS.max_concurrency, len(accounts)))
    devices = pick_devices(payload.devices) if SETTINGS.nftoken else []

    job = start(new_job(accounts, devices, concurrency, parse_proxy_text(payload.proxies)))
    return JSONResponse(
        {"job_id": job.id, "total": job.total, "skipped": skipped, "devices": devices, "concurrency": concurrency},
        status_code=202,
    )


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_token)])
async def read_job(job_id: str) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.public()


@app.get("/api/jobs/{job_id}/report.txt", dependencies=[Depends(require_token)], response_class=PlainTextResponse)
async def report_txt(job_id: str) -> PlainTextResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return PlainTextResponse(
        build_report(job),
        headers={"Content-Disposition": f'attachment; filename="netflix-report-{job_id}.txt"'},
    )


@app.get("/api/jobs/{job_id}/report.json", dependencies=[Depends(require_token)])
async def report_json(job_id: str) -> JSONResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(
        job.public(with_cookies=True),
        headers={"Content-Disposition": f'attachment; filename="netflix-report-{job_id}.json"'},
    )


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_token)])
async def cancel_job(job_id: str) -> dict:
    if not store.drop(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"cancelled": job_id}


@app.on_event("startup")
async def janitor() -> None:
    async def loop() -> None:
        while True:
            await asyncio.sleep(120)
            store.purge()

    asyncio.create_task(loop())
