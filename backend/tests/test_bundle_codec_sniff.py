"""Tests for the URL-extension codec sniffer used by the mobile bundle.

Small, targeted set — the sniffer is one function but it's on the
persisted-history/read-model boundary so getting the extension parsing
wrong would break the mobile client's decoder-selection logic.
"""

from __future__ import annotations

import pytest

from tone_forge_api import _codec_from_stem_url


class TestCodecFromStemUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            # R2 custom-domain public URLs
            ("https://cdn.tone-forge.test/bundles/abc/stems/drums.m4a", "m4a"),
            ("https://cdn.tone-forge.test/bundles/abc/stems/drums.wav", "wav"),
            # R2 native endpoint (presigned)
            (
                "https://acct.r2.cloudflarestorage.com/tone-forge-stems/"
                "bundles/abc/stems/drums.m4a?X-Amz-Signature=abc123",
                "m4a",
            ),
            (
                "https://acct.r2.cloudflarestorage.com/tone-forge-stems/"
                "bundles/abc/stems/vocals.wav?X-Amz-Signature=abc123",
                "wav",
            ),
            # Local serve-file fallback (never re-encoded)
            (
                "/api/admin/serve-file?path=/tmp/toneforge_stems/song_drums.wav",
                "wav",
            ),
            # HTTP loopback path (older analyses)
            ("http://127.0.0.1:7777/api/serve-file?path=/tmp/song_drums.wav", "wav"),
            # Unknown / missing → default to wav (that's what analysis emits)
            (None, "wav"),
            ("", "wav"),
            ("https://example.test/no-extension", "wav"),
            # Other formats we might handle later
            ("https://cdn.test/song.mp3", "mp3"),
            ("https://cdn.test/song.flac", "flac"),
            # Case-insensitive extension
            ("https://cdn.test/song.M4A", "m4a"),
        ],
    )
    def test_sniff(self, url, expected):
        assert _codec_from_stem_url(url) == expected
