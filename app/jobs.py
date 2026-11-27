"""In-memory job store and worker pool."""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from .core import (
    SETTINGS,
    build_links,
    check_account,
    create_nftoken,
    derive_plan,
    is_extra_member,
    is_on_hold,
    is_subscribed,
    random_offset,
    sort_key,
    to_netscape,
)


@dataclass
class Job:
    id: str
    devices: list[str]
    concurrency: int
    proxies: list[dict]
    accounts: list[dict[str, str]]
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

    def public(self, with_cookies: bool = False) -> dict[str, Any]:
        with self.lock:
            results = sorted(self.results, key=sort_key)
        keys = (
            "index", "status", "plan", "plan_label", "country", "email", "name", "streams",
            "quality", "price", "payment", "card", "profiles", "extra_member", "on_hold",
            "nftoken", "nftoken_expires", "links", "error", "duration_ms",
        )
        payload = {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "counters": self.counters(),
            "results": [{key: result.get(key) for key in keys} for result in results],
        }
        if with_cookies:
            for public, result in zip(payload["results"], results):
                public["cookies"] = result.get("cookies")
        return payload


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def drop(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job:
            job.cancelled = True
        return job is not None

    def active(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.status in {"queued", "running"})

    def purge(self) -> None:
        limit = time.time() - SETTINGS.ttl
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.status in {"done", "cancelled", "failed"} and (job.finished_at or job.created_at) < limit:
                    self._jobs.pop(job_id, None)


store = JobStore()


def _process(job: Job, index: int) -> None:
    if job.cancelled:
        return
    cookies = job.accounts[index]
    pool = job.proxies or list(SETTINGS.proxies)
    started = time.perf_counter()
    result: dict[str, Any] = {
        "index": index,
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
        outcome = check_account(cookies, pool, SETTINGS)
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
            payment=info.get("payment"),
            card=info.get("card"),
            profiles=info.get("profiles") or [],
            extra_member=extra,
            on_hold=is_on_hold(info),
        )

        if subscribed and job.devices:
            token, expiry = create_nftoken(cookies.get("NetflixId", ""), pool[random_offset(len(pool))] if pool else None)
            if token:
                result.update(nftoken=token, nftoken_expires=expiry, links=build_links(token, job.devices))
            else:
                result["error"] = f"nftoken: {expiry}"
    except Exception as exc:  # noqa: BLE001 - one bad cookie must not kill the job
        result.update(status="error", plan="error", plan_label="Error", error=f"{type(exc).__name__}: {exc}")
    finally:
        result["duration_ms"] = int((time.perf_counter() - started) * 1000)
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


def start(job: Job) -> Job:
    store.add(job)
    threading.Thread(target=_run, args=(job,), daemon=True, name=f"job-{job.id}").start()
    return job


def new_job(accounts: list[dict[str, str]], devices: list[str], concurrency: int, proxies: list[dict]) -> Job:
    return Job(
        id=uuid.uuid4().hex[:12],
        devices=devices,
        concurrency=concurrency,
        proxies=proxies,
        accounts=accounts,
    )


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
        lines += [
            "=" * 78,
            f"[{position}] {result['plan_label'].upper()} | {result.get('country') or '-'} | {result.get('email') or '-'}",
        ]
        for key, label in (("name", "Name"), ("streams", "Streams"), ("quality", "Quality"), ("price", "Price")):
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
