"""Remote GPU worker: run deep analysis for a hosted ToneForge backend.

The production backend (https://jamn.app) has no GPU. This worker runs
on the user's Mac, dials OUT over HTTPS, and drives the same subprocess
pipeline as the local /api/analyze-deep endpoint:

    claim job  ->  download source  ->  run_file_analysis (subprocess)
               ->  upload stems     ->  post result JSON

Outbound-only: no inbound port, no mixed-content problem in the
browser, works behind NAT. The claim long-poll doubles as the presence
heartbeat that lights up the "engine online" banner on the jam page.

Config (first hit wins):
    CLI flags            --backend https://jamn.app --token <secret>
    environment          TONEFORGE_BACKEND_URL / TONEFORGE_ENGINE_TOKEN
    ~/.toneforge/engine.json   {"backend_url": ..., "engine_token": ...}

Run:  python -m local_engine.remote_worker --backend https://jamn.app \
          --token <TONEFORGE_ENGINE_TOKEN> [--save]
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("toneforge.remote_worker")

_CONFIG_PATH = Path.home() / ".toneforge" / "engine.json"
_CLAIM_WAIT_SEC = 20.0
_CLAIM_TIMEOUT_SEC = 35.0
_RETRY_SLEEP_SEC = 5.0
# Stem files are large float32 wavs (~80 MB for a 4-minute song); on a
# contended uplink a single socket write can stall for minutes. Give
# uploads a long timeout and retry transient network failures instead
# of throwing away a finished GPU job.
_UPLOAD_TIMEOUT_SEC = 600
_UPLOAD_ATTEMPTS = 3
_UPLOAD_RETRY_SLEEP_SEC = 10.0
# Watchdog for the analysis subprocess. A healthy pipeline emits queue
# events (progress/result/done) continuously; a deadlocked child sits
# at 0% CPU emitting nothing while staying alive, which used to hang
# the worker forever (and let the backend stale-requeue the job to
# whatever claimant showed up next). Kill the child if it goes silent
# for _JOB_STALL_SEC, or exceeds _JOB_MAX_SEC wall clock outright.
_JOB_STALL_SEC = 15 * 60
_JOB_MAX_SEC = 60 * 60
# The engine's serve-file wrapper — stems_paths values arrive wrapped
# in this; strip it to recover the on-disk path.
_SERVE_PREFIXES = (
    "http://127.0.0.1:7777/api/serve-file?path=",
    "http://localhost:7777/api/serve-file?path=",
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(
    backend: Optional[str] = None, token: Optional[str] = None
) -> tuple[str, str]:
    """Resolve (backend_url, engine_token). Raises SystemExit when the
    backend URL can't be determined — a worker with no target is a bug,
    not a retry loop."""
    file_cfg: dict = {}
    if _CONFIG_PATH.is_file():
        try:
            file_cfg = json.loads(_CONFIG_PATH.read_text())
        except Exception:  # noqa: BLE001
            logger.warning("could not parse %s — ignoring", _CONFIG_PATH)
    backend_url = (
        backend
        or os.environ.get("TONEFORGE_BACKEND_URL")
        or file_cfg.get("backend_url")
        or ""
    ).rstrip("/")
    engine_token = (
        token
        or os.environ.get("TONEFORGE_ENGINE_TOKEN")
        or file_cfg.get("engine_token")
        or ""
    )
    if not backend_url:
        raise SystemExit(
            "No backend URL. Pass --backend https://jamn.app, set "
            "TONEFORGE_BACKEND_URL, or create ~/.toneforge/engine.json."
        )
    return backend_url, engine_token


def save_config(backend_url: str, engine_token: str) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(
        {"backend_url": backend_url, "engine_token": engine_token}, indent=2
    ))
    _CONFIG_PATH.chmod(0o600)
    logger.info("saved worker config to %s", _CONFIG_PATH)


def detect_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def sanitize_for_json(obj):
    """Recursively convert numpy types to Python natives for JSON encoding."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, (np.bool_, np.integer)):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return sanitize_for_json(obj.tolist())
    return obj


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class RemoteWorker:
    def __init__(self, backend_url: str, engine_token: str):
        self.backend_url = backend_url
        self.session = requests.Session()
        if engine_token:
            self.session.headers["X-Engine-Token"] = engine_token
        self.worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
        self.device = detect_device()
        self._stop = False

    # -- backend I/O ------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.backend_url}{path}"

    def claim(self) -> Optional[dict]:
        resp = self.session.post(
            self._url("/api/engine/claim"),
            json={
                "worker_id": self.worker_id,
                "device": self.device,
                "wait_sec": _CLAIM_WAIT_SEC,
            },
            timeout=_CLAIM_TIMEOUT_SEC,
        )
        if resp.status_code == 204:
            return None
        if resp.status_code == 404:
            raise PermissionError(
                "Backend rejected the engine token (404). Check "
                "TONEFORGE_ENGINE_TOKEN matches the server."
            )
        resp.raise_for_status()
        return resp.json()

    def post_progress(self, job_id: str, percent: float, message: str) -> None:
        try:
            self.session.post(
                self._url(f"/api/engine/job/{job_id}/progress"),
                json={"percent": percent, "message": message},
                timeout=10,
            )
        except requests.RequestException as e:
            logger.warning("progress post failed: %s", e)

    def post_fail(self, job_id: str, error: str) -> None:
        try:
            self.session.post(
                self._url(f"/api/engine/job/{job_id}/fail"),
                json={"error": error},
                timeout=10,
            )
        except requests.RequestException as e:
            logger.error("fail post failed: %s", e)

    def download_source(self, job_id: str, filename: str) -> Path:
        suffix = Path(filename or "").suffix or ".wav"
        resp = self.session.get(
            self._url(f"/api/engine/job/{job_id}/file"),
            stream=True, timeout=120,
        )
        resp.raise_for_status()
        with tempfile.NamedTemporaryFile(
            suffix=suffix, prefix="toneforge_remote_", delete=False
        ) as tmp:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                tmp.write(chunk)
            return Path(tmp.name)

    def upload_stems(self, job_id: str, stems_paths: dict) -> None:
        """Push each separated stem file to the backend."""
        roles = list(stems_paths.keys())
        for i, role in enumerate(roles):
            local = _local_stem_path(stems_paths[role])
            if local is None or not local.is_file():
                logger.warning("stem %s has no local file (%r) — skipped",
                               role, stems_paths[role])
                continue
            self.post_progress(
                job_id, 95 + 4 * (i / max(1, len(roles))),
                f"Uploading stem {i + 1}/{len(roles)} ({role})…",
            )
            self._upload_stem_with_retry(job_id, role, local)

    def _upload_stem_with_retry(self, job_id: str, role: str, local: Path) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(1, _UPLOAD_ATTEMPTS + 1):
            try:
                with local.open("rb") as f:
                    resp = self.session.post(
                        self._url(f"/api/engine/job/{job_id}/stem"),
                        data={"role": role},
                        files={"file": (local.name, f, "audio/wav")},
                        timeout=_UPLOAD_TIMEOUT_SEC,
                    )
                resp.raise_for_status()
                return
            except requests.RequestException as e:
                last_exc = e
                if attempt < _UPLOAD_ATTEMPTS:
                    logger.warning(
                        "stem %s upload attempt %d/%d failed (%s) — retrying in %ss",
                        role, attempt, _UPLOAD_ATTEMPTS, e, _UPLOAD_RETRY_SLEEP_SEC,
                    )
                    time.sleep(_UPLOAD_RETRY_SLEEP_SEC)
        raise RuntimeError(
            f"stem {role} upload failed after {_UPLOAD_ATTEMPTS} attempts: {last_exc}"
        )

    def post_complete(self, job_id: str, result: dict) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(1, _UPLOAD_ATTEMPTS + 1):
            try:
                resp = self.session.post(
                    self._url(f"/api/engine/job/{job_id}/complete"),
                    json=result, timeout=120,
                )
                resp.raise_for_status()
                return
            except requests.RequestException as e:
                last_exc = e
                if attempt < _UPLOAD_ATTEMPTS:
                    logger.warning(
                        "complete post attempt %d/%d failed (%s) — retrying in %ss",
                        attempt, _UPLOAD_ATTEMPTS, e, _UPLOAD_RETRY_SLEEP_SEC,
                    )
                    time.sleep(_UPLOAD_RETRY_SLEEP_SEC)
        raise RuntimeError(
            f"complete post failed after {_UPLOAD_ATTEMPTS} attempts: {last_exc}"
        )

    # -- job execution ----------------------------------------------------

    def run_job(self, job: dict) -> None:
        from local_engine.analysis_worker import run_file_analysis

        job_id = job["job_id"]
        filename = job.get("filename") or job.get("source_name") or "upload.wav"
        logger.info("claimed job %s (%s)", job_id, filename)

        source: Optional[Path] = None
        try:
            self.post_progress(job_id, 3, "Downloading source…")
            source = self.download_source(job_id, filename)

            queue: multiprocessing.Queue = multiprocessing.Queue()
            process = multiprocessing.Process(
                target=run_file_analysis,
                args=(str(source), queue, None, filename),
                daemon=True,
            )
            process.start()

            result_data: Optional[dict] = None
            last_sent = 0.0
            started = time.time()
            last_event = started
            while True:
                try:
                    event = queue.get(timeout=1.0)
                except Exception:  # noqa: BLE001  (queue.Empty)
                    if not process.is_alive():
                        break
                    now = time.time()
                    stalled = now - last_event > _JOB_STALL_SEC
                    over_cap = now - started > _JOB_MAX_SEC
                    if stalled or over_cap:
                        reason = (
                            f"no engine events for {int(now - last_event)}s"
                            if stalled
                            else f"exceeded {_JOB_MAX_SEC}s wall clock"
                        )
                        logger.error(
                            "job %s watchdog: %s — killing subprocess %s",
                            job_id, reason, process.pid,
                        )
                        process.kill()
                        process.join(timeout=10)
                        raise RuntimeError(
                            f"engine watchdog killed job: {reason}"
                        )
                    continue
                last_event = time.time()
                etype = event.get("type")
                if etype == "progress":
                    # Pipeline occupies 5–95 of the job bar (claim=2,
                    # stem upload=95–99, complete=100). Throttle to
                    # ~1 post/2 s — the SSE fan-out costs a version
                    # bump per update.
                    now = time.time()
                    if now - last_sent >= 2.0:
                        last_sent = now
                        pct = 5 + 90 * float(event.get("progress") or 0)
                        self.post_progress(
                            job_id, pct, event.get("message") or "Processing…"
                        )
                elif etype == "result":
                    result_data = event.get("data") or {}
                elif etype == "error":
                    raise RuntimeError(event.get("message") or "engine error")
                elif etype == "done":
                    break

            process.join(timeout=10)
            if result_data is None:
                raise RuntimeError("engine subprocess exited without a result")

            stems_paths = result_data.get("stems_paths") or {}
            self.upload_stems(job_id, stems_paths)

            # Localhost serve-file URLs are meaningless on the backend —
            # it rebuilds stems_paths from the uploads above.
            result_data.pop("stem_records", None)
            result_data.pop("stems", None)
            self.post_progress(job_id, 99, "Saving analysis…")
            self.post_complete(job_id, sanitize_for_json(result_data))
            logger.info("job %s complete", job_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("job %s failed", job_id)
            self.post_fail(job_id, str(e))
        finally:
            if source is not None:
                source.unlink(missing_ok=True)

    # -- main loop --------------------------------------------------------

    def run_forever(self) -> None:
        logger.info(
            "worker %s (%s) polling %s",
            self.worker_id, self.device, self.backend_url,
        )
        while not self._stop:
            try:
                job = self.claim()
            except PermissionError as e:
                raise SystemExit(str(e))
            except requests.RequestException as e:
                logger.warning("claim failed (%s) — retrying in %ss",
                               e, _RETRY_SLEEP_SEC)
                time.sleep(_RETRY_SLEEP_SEC)
                continue
            if job is not None:
                self.run_job(job)

    def stop(self) -> None:
        self._stop = True


def _local_stem_path(value: str) -> Optional[Path]:
    """Recover the on-disk path from a stems_paths value (serve-file
    wrapper or raw path)."""
    if not isinstance(value, str) or not value:
        return None
    for prefix in _SERVE_PREFIXES:
        if value.startswith(prefix):
            return Path(value[len(prefix):])
    if value.startswith(("http://", "https://")):
        return None
    return Path(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", help="Backend base URL, e.g. https://jamn.app")
    parser.add_argument("--token", help="TONEFORGE_ENGINE_TOKEN value")
    parser.add_argument(
        "--save", action="store_true",
        help="Persist backend/token to ~/.toneforge/engine.json",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    backend_url, engine_token = load_config(args.backend, args.token)
    if args.save:
        save_config(backend_url, engine_token)

    worker = RemoteWorker(backend_url, engine_token)
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("worker stopped")


if __name__ == "__main__":
    main()
