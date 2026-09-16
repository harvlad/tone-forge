"""Early stem uploads must carry the engine auth.

The early-upload threads open their OWN requests.Session (connection
safety), but a bare Session has no X-Engine-Token header — prod's engine
guard 404s ("Not Found") every early upload, each stem burned 6 retries
x 20 s, and the authed tail-save re-uploaded everything: ~2-4.5 min added
to EVERY prod analysis, invisibly. Dev never caught it because with no
token configured the guard allows loopback, so early upload "worked" in
every local test. This pins the header propagation.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from local_engine.remote_worker import RemoteWorker


def test_early_upload_session_carries_engine_token(tmp_path, monkeypatch):
    w = RemoteWorker("https://example.test", engine_token="sekrit")

    # A real (tiny) stem file so the path checks pass.
    stem = tmp_path / "drums.wav"
    stem.write_bytes(b"RIFF0000WAVE")

    seen = {}

    def _capture(self, job_id, role, local, session=None):
        seen["headers"] = dict(session.headers) if session is not None else None

    monkeypatch.setattr(RemoteWorker, "_upload_stem_with_retry", _capture)
    # Skip the ffmpeg FLAC compression — irrelevant to the auth contract.
    monkeypatch.setattr(RemoteWorker, "_compress_lossless",
                        staticmethod(lambda local: local))

    assert w._upload_one_stem_own_session("job123", "drums", str(stem)) is True
    assert seen["headers"] is not None, "upload must use an explicit session"
    assert seen["headers"].get("X-Engine-Token") == "sekrit", (
        "early-upload session dropped the engine token — prod 404s every "
        "early stem upload"
    )
