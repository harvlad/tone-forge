"""Tests for the R2 storage helpers.

We don't hit real R2. The tests exercise:
  1. Behaviour when env vars are missing (must not crash).
  2. Behaviour when boto3 is missing (must not crash).
  3. Key layout is deterministic.
  4. Content-type inference for the audio formats we care about.
  5. Upload path calls boto3 with the right args (via a stub client).

A separate integration test — gated on ``TONEFORGE_R2_INTEGRATION=1`` —
does a real round-trip. That's off by default so CI doesn't need creds.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tone_forge import r2_storage


@pytest.fixture(autouse=True)
def _clean_cache():
    """The lazy boto3 client is memoised via lru_cache; clear it around
    every test so credential-env changes take effect immediately."""
    r2_storage._client.cache_clear()
    yield
    r2_storage._client.cache_clear()


@pytest.fixture
def clear_r2_env(monkeypatch: pytest.MonkeyPatch):
    for var in (
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "R2_PUBLIC_HOST",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def r2_env(monkeypatch: pytest.MonkeyPatch, clear_r2_env):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct_test")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secretshh")
    monkeypatch.setenv("R2_BUCKET", "tf-test")


class TestIsConfigured:
    def test_no_env_returns_false(self, clear_r2_env):
        assert r2_storage.is_configured() is False

    def test_full_env_returns_true(self, r2_env):
        assert r2_storage.is_configured() is True

    def test_missing_secret_returns_false(
        self, monkeypatch: pytest.MonkeyPatch, r2_env
    ):
        monkeypatch.delenv("R2_SECRET_ACCESS_KEY")
        assert r2_storage.is_configured() is False


class TestBucketAndKey:
    def test_bucket_default(self, clear_r2_env):
        assert r2_storage.bucket_name() == "tone-forge-stems"

    def test_bucket_from_env(self, r2_env):
        assert r2_storage.bucket_name() == "tf-test"

    def test_stem_key_layout(self):
        assert (
            r2_storage.stem_key("abc12345", "drums", "wav")
            == "bundles/abc12345/stems/drums.wav"
        )

    def test_stem_key_strips_leading_dot(self):
        # Callers pass Path.suffix which starts with a dot.
        assert (
            r2_storage.stem_key("abc", "bass", ".m4a")
            == "bundles/abc/stems/bass.m4a"
        )

    def test_stem_key_sanitises_role(self):
        # Roles come from the analysis pipeline which sometimes emits
        # slashes (e.g. guitar_center vs guitar/center). We want the
        # object key to be filesystem-safe.
        assert (
            r2_storage.stem_key("abc", "guitar/center", ".wav")
            == "bundles/abc/stems/guitar_center.wav"
        )


class TestContentType:
    def test_wav(self):
        assert r2_storage._content_type_for(Path("stem.wav")) == "audio/wav"

    def test_m4a(self):
        assert r2_storage._content_type_for(Path("stem.m4a")) == "audio/mp4"

    def test_unknown(self):
        assert (
            r2_storage._content_type_for(Path("stem.unknownext"))
            == "application/octet-stream"
        )


class TestUploadStem:
    def test_no_env_returns_none(self, clear_r2_env, tmp_path: Path):
        wav = tmp_path / "drums.wav"
        wav.write_bytes(b"fake wav")
        assert r2_storage.upload_stem(wav, "abc", "drums") is None

    def test_missing_file_returns_none(self, r2_env):
        assert (
            r2_storage.upload_stem("/does/not/exist.wav", "abc", "drums")
            is None
        )

    def test_boto3_import_failure_returns_none(
        self, r2_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # Simulate boto3 not installed: the exception inside _client()
        # is caught by upload_stem's broad except and returns None.
        wav = tmp_path / "drums.wav"
        wav.write_bytes(b"fake")
        r2_storage._client.cache_clear()
        with patch.object(
            r2_storage,
            "_client",
            side_effect=ImportError("no boto3"),
        ):
            assert r2_storage.upload_stem(wav, "abc", "drums") is None

    def test_upload_happy_path_uploads_and_returns_url(
        self,
        r2_env,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        wav = tmp_path / "drums.wav"
        wav.write_bytes(b"data")
        monkeypatch.setenv("R2_PUBLIC_HOST", "cdn.example.test")

        fake_client = MagicMock()
        # object_exists → HEAD returns 404
        from botocore.exceptions import ClientError

        fake_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "not found"}},
            "HeadObject",
        )
        with patch.object(r2_storage, "_client", return_value=fake_client):
            url = r2_storage.upload_stem(wav, "abc12345", "drums")

        assert url == "https://cdn.example.test/bundles/abc12345/stems/drums.wav"
        fake_client.upload_file.assert_called_once()
        kwargs = fake_client.upload_file.call_args.kwargs
        assert kwargs["Bucket"] == "tf-test"
        assert kwargs["Key"] == "bundles/abc12345/stems/drums.wav"
        assert kwargs["ExtraArgs"]["ContentType"] == "audio/wav"
        assert "immutable" in kwargs["ExtraArgs"]["CacheControl"]

    def test_upload_skipped_when_object_exists(
        self,
        r2_env,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        wav = tmp_path / "drums.wav"
        wav.write_bytes(b"data")
        monkeypatch.setenv("R2_PUBLIC_HOST", "cdn.example.test")

        fake_client = MagicMock()
        # HEAD succeeds → object already there → no upload_file call.
        fake_client.head_object.return_value = {"ContentLength": 4}
        with patch.object(r2_storage, "_client", return_value=fake_client):
            url = r2_storage.upload_stem(wav, "abc", "drums")

        assert url == "https://cdn.example.test/bundles/abc/stems/drums.wav"
        fake_client.upload_file.assert_not_called()


class TestPublicUrl:
    def test_public_host_used_when_set(
        self, r2_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("R2_PUBLIC_HOST", "cdn.example.test")
        url = r2_storage.public_url_for("bundles/abc/stems/drums.wav")
        assert url == "https://cdn.example.test/bundles/abc/stems/drums.wav"

    def test_public_host_strips_trailing_slash(
        self, r2_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("R2_PUBLIC_HOST", "cdn.example.test/")
        url = r2_storage.public_url_for("bundles/abc/stems/drums.wav")
        assert url == "https://cdn.example.test/bundles/abc/stems/drums.wav"

    def test_falls_back_to_presign_without_public_host(
        self, r2_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("R2_PUBLIC_HOST", raising=False)
        fake_client = MagicMock()
        fake_client.generate_presigned_url.return_value = (
            "https://acct_test.r2.cloudflarestorage.com/tf-test/"
            "bundles/abc/stems/drums.wav?X-Amz-Signature=…"
        )
        with patch.object(r2_storage, "_client", return_value=fake_client):
            url = r2_storage.public_url_for("bundles/abc/stems/drums.wav")
        assert url.startswith("https://acct_test.r2.cloudflarestorage.com/")
        fake_client.generate_presigned_url.assert_called_once()


class TestKeyFromUrl:
    def test_presigned_path_style(self, r2_env):
        url = (
            "https://acct_test.r2.cloudflarestorage.com/tf-test/"
            "bundles/abc/stems/drums.m4a?X-Amz-Signature=deadbeef"
            "&X-Amz-Expires=604800"
        )
        assert r2_storage.key_from_url(url) == "bundles/abc/stems/drums.m4a"

    def test_presigned_virtual_hosted_style(self, r2_env):
        url = (
            "https://tf-test.acct_test.r2.cloudflarestorage.com/"
            "bundles/abc/stems/bass.wav?X-Amz-Signature=deadbeef"
        )
        assert r2_storage.key_from_url(url) == "bundles/abc/stems/bass.wav"

    def test_public_host_form(self, r2_env, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("R2_PUBLIC_HOST", "cdn.example.test")
        url = "https://cdn.example.test/bundles/abc/stems/vocals.m4a"
        assert r2_storage.key_from_url(url) == "bundles/abc/stems/vocals.m4a"

    def test_other_bucket_rejected(self, r2_env):
        url = (
            "https://acct_test.r2.cloudflarestorage.com/someone-elses-bucket/"
            "bundles/abc/stems/drums.m4a?X-Amz-Signature=x"
        )
        assert r2_storage.key_from_url(url) is None

    def test_third_party_url_rejected(self, r2_env):
        assert r2_storage.key_from_url("https://example.com/a.wav") is None

    def test_non_https_rejected(self, r2_env):
        assert (
            r2_storage.key_from_url(
                "http://127.0.0.1:7777/api/serve-file?path=/tmp/x.wav"
            )
            is None
        )
        assert r2_storage.key_from_url("/tmp/drums.wav") is None
        assert r2_storage.key_from_url(None) is None
        assert r2_storage.key_from_url("") is None

    def test_url_encoded_key_decoded(self, r2_env):
        url = (
            "https://acct_test.r2.cloudflarestorage.com/tf-test/"
            "bundles/abc/stems/guitar%20center.wav?X-Amz-Signature=x"
        )
        assert (
            r2_storage.key_from_url(url)
            == "bundles/abc/stems/guitar center.wav"
        )


class TestRefreshUrl:
    _STALE = (
        "https://acct_test.r2.cloudflarestorage.com/tf-test/"
        "bundles/abc/stems/drums.m4a?X-Amz-Signature=stale"
        "&X-Amz-Expires=604800"
    )

    def test_represigns_our_url(self, r2_env, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("R2_PUBLIC_HOST", raising=False)
        fake_client = MagicMock()
        fake_client.generate_presigned_url.return_value = (
            "https://acct_test.r2.cloudflarestorage.com/tf-test/"
            "bundles/abc/stems/drums.m4a?X-Amz-Signature=fresh"
        )
        with patch.object(r2_storage, "_client", return_value=fake_client):
            fresh = r2_storage.refresh_url(self._STALE)
        assert "X-Amz-Signature=fresh" in fresh
        kwargs = fake_client.generate_presigned_url.call_args.kwargs
        assert kwargs["Params"]["Key"] == "bundles/abc/stems/drums.m4a"
        assert kwargs["ExpiresIn"] == r2_storage._READ_PRESIGN_TTL_SEC

    def test_public_host_wins_over_presign(
        self, r2_env, monkeypatch: pytest.MonkeyPatch
    ):
        # Once the custom domain lands, refresh should upgrade old
        # presigned URLs to permanent public ones — no client change.
        monkeypatch.setenv("R2_PUBLIC_HOST", "cdn.example.test")
        fresh = r2_storage.refresh_url(self._STALE)
        assert fresh == (
            "https://cdn.example.test/bundles/abc/stems/drums.m4a"
        )

    def test_foreign_url_passes_through(self, r2_env):
        url = "https://example.com/song.wav"
        assert r2_storage.refresh_url(url) == url

    def test_unconfigured_passes_through(self, clear_r2_env):
        # key_from_url can't even match without env; must not crash.
        assert r2_storage.refresh_url(self._STALE) == self._STALE

    def test_presign_failure_returns_original(
        self, r2_env, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("R2_PUBLIC_HOST", raising=False)
        fake_client = MagicMock()
        fake_client.generate_presigned_url.side_effect = RuntimeError("boom")
        with patch.object(r2_storage, "_client", return_value=fake_client):
            assert r2_storage.refresh_url(self._STALE) == self._STALE


@pytest.mark.skipif(
    os.environ.get("TONEFORGE_R2_INTEGRATION") != "1",
    reason="opt-in R2 integration test — set TONEFORGE_R2_INTEGRATION=1",
)
class TestR2Integration:
    """Real round-trip against a live bucket.

    Off by default. To run:
        TONEFORGE_R2_INTEGRATION=1 R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=...
        R2_SECRET_ACCESS_KEY=... R2_BUCKET=tf-ci pytest tests/test_r2_storage.py::TestR2Integration
    """

    def test_upload_and_head_roundtrip(self, tmp_path: Path):
        assert r2_storage.is_configured(), "credentials must be set"
        wav = tmp_path / "ci_probe.wav"
        wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")  # 12-byte stub
        url = r2_storage.upload_stem(wav, "ci_probe", "test")
        assert url is not None
        key = r2_storage.stem_key("ci_probe", "test", ".wav")
        assert r2_storage.object_exists(key) is True
