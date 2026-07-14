"""Magic-link rate limiting: per-email and per-IP sliding windows."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api  # noqa: E402
from tone_forge.auth import email_sender, rate_limit  # noqa: E402


@pytest.fixture
def client():
    rate_limit.reset()
    email_sender.sent_emails.clear()
    with TestClient(api.app) as c:
        yield c


def _post(client, email, ip=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post(
        "/api/auth/magic-link", json={"email": email}, headers=headers
    )


class TestRateLimit:
    def test_email_limit_3_per_window(self, client):
        for _ in range(3):
            assert _post(client, "a@b.co").status_code == 202
        resp = _post(client, "a@b.co")
        assert resp.status_code == 429
        assert int(resp.headers["Retry-After"]) > 0
        # Different email unaffected
        assert _post(client, "c@d.co").status_code == 202

    def test_email_limit_case_insensitive(self, client):
        for e in ("A@b.co", "a@B.co", "a@b.co"):
            assert _post(client, e).status_code == 202
        assert _post(client, "A@B.CO").status_code == 429

    def test_ip_limit_10_per_window(self, client):
        for i in range(10):
            assert _post(client, f"u{i}@x.co", ip="9.9.9.9").status_code == 202
        resp = _post(client, "u10@x.co", ip="9.9.9.9")
        assert resp.status_code == 429
        # Different IP unaffected
        assert _post(client, "u11@x.co", ip="8.8.8.8").status_code == 202

    def test_first_forwarded_hop_used(self, client):
        for i in range(10):
            _post(client, f"v{i}@x.co", ip="7.7.7.7")
        resp = client.post(
            "/api/auth/magic-link",
            json={"email": "v10@x.co"},
            headers={"X-Forwarded-For": "7.7.7.7, 1.1.1.1"},
        )
        assert resp.status_code == 429

    def test_window_expiry_frees_slot(self, client):
        import time

        for _ in range(3):
            _post(client, "w@x.co")
        # Age the oldest hit past the window
        rate_limit._email_hits["w@x.co"][0] = (
            time.time() - rate_limit.WINDOW_SEC - 1
        )
        assert _post(client, "w@x.co").status_code == 202

    def test_rate_limited_sends_no_email(self, client):
        for _ in range(3):
            _post(client, "a@b.co")
        n = len(email_sender.sent_emails)
        _post(client, "a@b.co")
        assert len(email_sender.sent_emails) == n
