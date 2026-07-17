"""Async analysis job registry.

Decouples an analysis run from the HTTP request that started it. A job
is created, an ``asyncio`` task drives the existing streaming pipeline,
and clients attach/detach freely:

  * ``GET /api/job/{id}``          -> current snapshot (cheap poll)
  * ``GET /api/job/{id}/events``   -> reconnectable SSE, live percent
  * ``GET /api/job/{id}/result``   -> long-poll until done/error

Single-process uvicorn => an in-memory dict is authoritative. Each state
transition is mirrored to ``data/jobs/{id}.json`` so a crash/restart can
mark interrupted jobs as errored instead of leaving clients polling a
job that will never finish.

No FastAPI dependency here on purpose — the endpoints in
``tone_forge_api`` own the wiring; this module is pure state + notify.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TERMINAL = ("done", "error")
_DEFAULT_TTL_SEC = 7 * 24 * 3600  # match history retention window


@dataclass
class JobState:
    id: str
    status: str = "queued"  # queued | running | done | error
    percent: float = 0.0
    message: str = ""
    history_id: Optional[str] = None
    error: Optional[str] = None
    filename: Optional[str] = None
    device_token: Optional[str] = None
    #: Client declared the user attested to owning/controlling rights to
    #: the uploaded audio (the mobile app gates uploads behind an
    #: AttestationSheet). Recorded server-side for the compliance trail.
    attested: bool = False
    #: Who executes the job. "server" jobs are driven by an asyncio task
    #: in this process; "engine" jobs sit queued until a remote GPU
    #: worker claims them via /api/engine/claim.
    kind: str = "server"
    #: Engine-job inputs/outputs (upload path, options, received stem
    #: paths). Never exposed via public_dict.
    payload: Optional[dict] = None
    #: Ownership (optional sign-in). ``device_id`` is the client's
    #: persistent X-Device-Id; ``owner_id`` is the account that owned the
    #: device (or was signed in) when the job was created. Neither is
    #: exposed via public_dict.
    device_id: Optional[str] = None
    owner_id: Optional[str] = None
    #: Attribution metadata resolved at submit time (title, artist,
    #: license, license_url, source_url, attribution — empty string
    #: means unknown). Stamped into the history entry by whichever
    #: writer completes the job. Never exposed via public_dict.
    meta: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    version: int = 0

    def public_dict(self) -> dict:
        """Client-facing shape. ``id`` is surfaced as ``job_id`` to match
        the submit response; ``device_token`` is never exposed.

        All values are coerced to native Python types to ensure JSON
        serialization works even if numpy types crept in from analysis.
        """
        return {
            "job_id": str(self.id),
            "status": str(self.status),
            "percent": float(self.percent),
            "message": str(self.message) if self.message else "",
            "history_id": str(self.history_id) if self.history_id else None,
            "error": str(self.error) if self.error else None,
            "filename": str(self.filename) if self.filename else None,
            "attested": bool(self.attested),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
            "version": int(self.version),
        }


_FIELDS = set(JobState.__dataclass_fields__.keys())


class JobRegistry:
    """In-memory job store with disk mirror and per-job change notify."""

    def __init__(self, jobs_dir: Path, ttl_sec: float = _DEFAULT_TTL_SEC):
        self._jobs: dict[str, JobState] = {}
        self._conds: dict[str, asyncio.Condition] = {}
        self._dir = jobs_dir
        self._ttl = ttl_sec
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            logger.exception("could not create jobs dir %s", self._dir)

    # -- lookup ----------------------------------------------------------

    def get(self, job_id: str) -> Optional[JobState]:
        return self._jobs.get(job_id)

    def all(self) -> list[JobState]:
        """Snapshot of every live job (list endpoint / diagnostics)."""
        return list(self._jobs.values())

    def queued_engine_positions(self) -> dict[str, int]:
        """job_id -> 1-based FIFO position among queued engine jobs.

        Same ordering ``next_queued_engine_job`` claims in, so the
        position is an honest "you are Nth in line".
        """
        queued = sorted(
            (j for j in self._jobs.values()
             if j.kind == "engine" and j.status == "queued"),
            key=lambda j: j.created_at,
        )
        return {j.id: i + 1 for i, j in enumerate(queued)}

    def create(
        self,
        filename: Optional[str] = None,
        attested: bool = False,
        device_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> JobState:
        job = JobState(
            id=uuid.uuid4().hex[:12],
            filename=filename,
            attested=attested,
            device_id=device_id,
            owner_id=owner_id,
            meta=meta,
        )
        self._jobs[job.id] = job
        self._persist(job)
        return job

    def create_engine_job(
        self,
        filename: Optional[str] = None,
        attested: bool = False,
        payload: Optional[dict] = None,
        device_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> JobState:
        """Queue a job for a remote GPU worker (claimed, not driven)."""
        job = JobState(
            id=uuid.uuid4().hex[:12],
            filename=filename,
            attested=attested,
            kind="engine",
            device_id=device_id,
            owner_id=owner_id,
            payload=payload or {},
            meta=meta,
        )
        self._jobs[job.id] = job
        self._persist(job)
        return job

    def next_queued_engine_job(self, stale_after_sec: float = 180.0) -> Optional[JobState]:
        """Oldest claimable engine job.

        A ``running`` engine job whose worker went silent for
        ``stale_after_sec`` (crash, network drop) is requeued first so a
        reconnecting worker picks it back up instead of it being stuck
        ``running`` forever.
        """
        now = time.time()
        engine_jobs = sorted(
            (j for j in self._jobs.values() if j.kind == "engine"),
            key=lambda j: j.created_at,
        )
        for job in engine_jobs:
            if job.status == "running" and now - job.updated_at > stale_after_sec:
                job.status = "queued"
                job.message = "Requeued after worker went silent"
                job.version += 1
                job.updated_at = now
                self._persist(job)
        for job in engine_jobs:
            if job.status == "queued":
                return job
        return None

    # -- mutation + notify ----------------------------------------------

    async def update(self, job_id: str, **fields) -> None:
        """Apply field changes, bump version, persist, wake waiters.

        The version bump + notify happen under the same condition lock
        that ``wait`` re-checks under, closing the lost-wakeup window.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return
        cond = self._cond(job_id)
        async with cond:
            for key, value in fields.items():
                setattr(job, key, value)
            job.version += 1
            job.updated_at = time.time()
            self._persist(job)
            cond.notify_all()

    async def wait(
        self, job_id: str, since_version: int, timeout: float
    ) -> Optional[JobState]:
        """Return the job once its version advances past ``since_version``
        or it reaches a terminal state; else return the current snapshot
        after ``timeout`` seconds (heartbeat / long-poll tick)."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        cond = self._cond(job_id)
        async with cond:
            if job.version > since_version or job.status in _TERMINAL:
                return job
            try:
                await asyncio.wait_for(cond.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
        return self._jobs.get(job_id)

    # -- startup recovery / cleanup -------------------------------------

    def recover(self) -> None:
        """Load persisted jobs. Interrupted (``running``/``queued``) jobs
        become ``error``; files older than the TTL are removed."""
        if not self._dir.exists():
            return
        now = time.time()
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                path.unlink(missing_ok=True)
                continue
            created = data.get("created_at", now)
            if now - created > self._ttl:
                path.unlink(missing_ok=True)
                continue
            job = JobState(**{k: v for k, v in data.items() if k in _FIELDS})
            if job.status in ("running", "queued"):
                if job.kind == "engine":
                    # Engine jobs aren't driven by an in-process task —
                    # the uploaded source file persists on disk, so the
                    # job can simply be (re)claimed by a worker.
                    job.status = "queued"
                    job.message = "Requeued after server restart"
                else:
                    job.status = "error"
                    job.error = "interrupted by server restart"
                job.version += 1
                job.updated_at = now
                self._persist(job)
            self._jobs[job.id] = job

    def sweep(self) -> int:
        """Drop terminal jobs older than the TTL from memory and disk.

        ``recover`` only prunes at startup; a long-running server needs
        this called periodically (the API's retention loop does) or the
        ``_jobs``/``_conds`` dicts grow without bound. Only terminal
        jobs are eligible — an in-flight job past its TTL still has a
        driver task and possibly attached clients.
        """
        now = time.time()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in _TERMINAL and now - job.created_at > self._ttl
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)
            self._conds.pop(job_id, None)
            (self._dir / f"{job_id}.json").unlink(missing_ok=True)
        if expired:
            logger.info("job sweep removed %d expired jobs", len(expired))
        return len(expired)

    # -- internals -------------------------------------------------------

    def _cond(self, job_id: str) -> asyncio.Condition:
        cond = self._conds.get(job_id)
        if cond is None:
            cond = asyncio.Condition()
            self._conds[job_id] = cond
        return cond

    def _persist(self, job: JobState) -> None:
        try:
            data = asdict(job)
            # Coerce numpy types to native Python for JSON serialization
            for key, value in data.items():
                if hasattr(value, "item"):  # numpy scalar
                    data[key] = value.item()
            (self._dir / f"{job.id}.json").write_text(json.dumps(data))
        except Exception:  # noqa: BLE001
            logger.exception("could not persist job %s", job.id)
