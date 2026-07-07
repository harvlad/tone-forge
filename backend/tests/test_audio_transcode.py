"""Tests for the ffmpeg AAC transcoder.

We don't invoke real ffmpeg. subprocess.run is monkeypatched to simulate
success (drops a stub m4a in the expected temp path), failure (raises
CalledProcessError), and timeout. That's enough to exercise the caching
behaviour, atomic-rename path, and the graceful-fallback contract.

One test flavour DOES require real ffmpeg — opt in with
``TONEFORGE_FFMPEG_INTEGRATION=1``. It transcodes a tiny generated WAV
and asserts the output has the right shape.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tone_forge import audio_transcode


@pytest.fixture(autouse=True)
def _clean_cache():
    audio_transcode.is_ffmpeg_available.cache_clear()
    yield
    audio_transcode.is_ffmpeg_available.cache_clear()


@pytest.fixture
def wav_path(tmp_path: Path) -> Path:
    p = tmp_path / "drums.wav"
    # 44-byte fake WAV header + tiny payload. transcode_to_m4a doesn't
    # actually read the bytes (ffmpeg would); the mocked run just needs
    # a valid input path to check.
    p.write_bytes(b"RIFF" + b"\x00" * 40 + b"WAVE" + b"\x00\x00\x00\x00")
    return p


class TestIsFfmpegAvailable:
    def test_when_present(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            audio_transcode.shutil, "which", lambda _: "/usr/local/bin/ffmpeg"
        )
        audio_transcode.is_ffmpeg_available.cache_clear()
        assert audio_transcode.is_ffmpeg_available() is True

    def test_when_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(audio_transcode.shutil, "which", lambda _: None)
        audio_transcode.is_ffmpeg_available.cache_clear()
        assert audio_transcode.is_ffmpeg_available() is False


class TestCacheKey:
    def test_stable_for_same_file(self, wav_path: Path):
        k1 = audio_transcode._cache_key(wav_path)
        k2 = audio_transcode._cache_key(wav_path)
        assert k1 == k2
        assert len(k1) == 16

    def test_changes_when_content_changes(self, wav_path: Path):
        k1 = audio_transcode._cache_key(wav_path)
        # Touch: rewriting with different bytes changes size and mtime.
        wav_path.write_bytes(b"different bytes entirely!!")
        # os stat mtime resolution can be coarse on some FSes; force
        # mtime forward to make the change observable.
        import time as _t
        _t.sleep(0.01)
        wav_path.touch()
        k2 = audio_transcode._cache_key(wav_path)
        assert k1 != k2


class TestTranscodeToM4a:
    def test_returns_none_when_ffmpeg_missing(
        self, wav_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(audio_transcode.shutil, "which", lambda _: None)
        audio_transcode.is_ffmpeg_available.cache_clear()
        assert audio_transcode.transcode_to_m4a(wav_path) is None

    def test_returns_none_when_source_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            audio_transcode.shutil, "which", lambda _: "/usr/local/bin/ffmpeg"
        )
        audio_transcode.is_ffmpeg_available.cache_clear()
        assert (
            audio_transcode.transcode_to_m4a(tmp_path / "nope.wav")
            is None
        )

    def test_happy_path_invokes_ffmpeg_and_returns_cache_path(
        self, wav_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            audio_transcode.shutil, "which", lambda _: "/usr/local/bin/ffmpeg"
        )
        audio_transcode.is_ffmpeg_available.cache_clear()

        # Simulate ffmpeg writing the .partial file.
        def fake_run(cmd, **kwargs):
            # Find the -y ... final positional arg (last one is the output).
            out_path = Path(cmd[-1])
            out_path.write_bytes(b"m4a payload stub")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with patch.object(audio_transcode.subprocess, "run", side_effect=fake_run) as m:
            dst = audio_transcode.transcode_to_m4a(wav_path)

        assert dst is not None
        assert dst.suffix == ".m4a"
        assert dst.is_file()
        assert dst.stat().st_size > 0

        # Argument shape sanity.
        args = m.call_args.args[0]
        assert args[0] == "ffmpeg"
        assert "-y" in args
        assert "-i" in args
        assert str(wav_path) in args
        assert "-c:a" in args and args[args.index("-c:a") + 1] == "aac"
        assert "-b:a" in args and args[args.index("-b:a") + 1] == audio_transcode.AAC_BITRATE
        assert "+faststart" in args[args.index("-movflags") + 1]

    def test_second_call_hits_cache(
        self, wav_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            audio_transcode.shutil, "which", lambda _: "/usr/local/bin/ffmpeg"
        )
        audio_transcode.is_ffmpeg_available.cache_clear()

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"m4a payload")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with patch.object(
            audio_transcode.subprocess, "run", side_effect=fake_run
        ) as m:
            first = audio_transcode.transcode_to_m4a(wav_path)
            second = audio_transcode.transcode_to_m4a(wav_path)

        assert first == second
        # ffmpeg only ran once — the second call short-circuited on cache.
        assert m.call_count == 1

    def test_ffmpeg_failure_returns_none_and_cleans_partial(
        self, wav_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            audio_transcode.shutil, "which", lambda _: "/usr/local/bin/ffmpeg"
        )
        audio_transcode.is_ffmpeg_available.cache_clear()

        def fake_run(cmd, **kwargs):
            # Simulate ffmpeg starting to write, then dying.
            Path(cmd[-1]).write_bytes(b"partial garbage")
            raise subprocess.CalledProcessError(
                1, cmd, output=b"", stderr=b"Invalid data found when processing input"
            )

        with patch.object(audio_transcode.subprocess, "run", side_effect=fake_run):
            result = audio_transcode.transcode_to_m4a(wav_path)

        assert result is None
        # The .partial file must not be left behind — otherwise a later
        # transcode call would happily reuse the garbage.
        partial = audio_transcode.cached_m4a_path(wav_path).with_suffix(".m4a.partial")
        assert not partial.exists()

    def test_ffmpeg_timeout_returns_none(
        self, wav_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            audio_transcode.shutil, "which", lambda _: "/usr/local/bin/ffmpeg"
        )
        audio_transcode.is_ffmpeg_available.cache_clear()

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, timeout=1)

        with patch.object(audio_transcode.subprocess, "run", side_effect=fake_run):
            assert audio_transcode.transcode_to_m4a(wav_path) is None


@pytest.mark.skipif(
    os.environ.get("TONEFORGE_FFMPEG_INTEGRATION") != "1",
    reason="opt-in ffmpeg integration test — set TONEFORGE_FFMPEG_INTEGRATION=1",
)
class TestFfmpegIntegration:
    def test_real_wav_transcodes(self, tmp_path: Path):
        import numpy as np
        import soundfile as sf

        # 1 second of 440 Hz sine at 44.1 kHz stereo.
        sr = 44100
        t = np.linspace(0, 1.0, sr, endpoint=False)
        sine = 0.2 * np.sin(2 * np.pi * 440.0 * t).astype("float32")
        stereo = np.stack([sine, sine], axis=1)
        wav = tmp_path / "probe.wav"
        sf.write(str(wav), stereo, sr)

        out = audio_transcode.transcode_to_m4a(wav)
        assert out is not None and out.is_file()
        assert out.stat().st_size > 1024   # at least 1 KB of AAC data
        assert out.suffix == ".m4a"
