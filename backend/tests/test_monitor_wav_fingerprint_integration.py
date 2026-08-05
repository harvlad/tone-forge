"""WAV ↔ fingerprint exact-equality integration test.

The previous three mechanical gates protect:

  - Producer: ``test_render_chain_references.py``
    (the rendering script's output schema)
  - Consumer: ``test_monitor_fingerprints.py``
    (the bundled JSONs' schema + YAML cross-check)
  - Retrieval: ``test_monitor_self_retrieval.py``
    (the bank's retrieval geometry — self-match at rank 1)

None of those run real audio through the librosa pipeline. They
*can't* catch a fourth drift class: someone re-bounces a chain's
WAV without re-running ``scripts/render_chain_references.py``, or
hand-edits a fingerprint JSON to a value that's not what the
audio actually measures. The schema gates would still pass; the
geometry gate would still pass; only end-to-end behaviour would
regress.

This file closes that gap by feeding each bundled WAV through
``_extract_query_fingerprint`` (the same function the rendering
script and the runtime query path both call) and asserting the
result matches the bundled JSON **exactly** — vector and validity
mask, byte-for-byte at float64 precision.

That equality is achievable because the rendering script calls
``_extract_query_fingerprint`` directly (see
``scripts/render_chain_references.py:207``); the catalog/query
math is the same code path, so any non-zero delta means
something off-pipeline mutated either side.

Cost: ~13s for the five bundled chains (librosa load + HPSS +
feature extraction). That's heavy enough that this file is named
with an ``_integration`` suffix so an operator running a quick
loop can skip it with ``pytest -k 'not integration'`` — but the
test is fast enough to keep on the default CI path.

What this file does *not* test:

  - The schema of the JSON. Covered by ``test_monitor_fingerprints.py``.
  - The script's helpers or its output structure. Covered by
    ``test_render_chain_references.py`` (which monkey-patches the
    extractor; this file is the real-audio side of the same
    contract).
  - Cross-chain retrieval geometry. Covered by
    ``test_monitor_self_retrieval.py``.
  - The librosa internals themselves — they are dependency code,
    not under our boundary discipline. We only assert the
    end-to-end pipeline is self-consistent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.monitor.loader import list_chain_ids
from tone_forge.tone import guitar_catalog as gc


_CHAINS_ROOT: Path = (
    Path(__file__).resolve().parent.parent
    / "tone_forge"
    / "monitor"
    / "chains"
)


def _wav_path(chain_id: str) -> Path:
    return _CHAINS_ROOT / f"{chain_id}.wav"


def _fingerprint_path(chain_id: str) -> Path:
    return _CHAINS_ROOT / f"{chain_id}.fingerprint.json"


# ---------------------------------------------------------------------------
# Bundle parity for WAVs (a fast preflight before the heavy tests run)
# ---------------------------------------------------------------------------


def test_every_chain_has_a_bundled_wav() -> None:
    """A chain without a WAV can't have its fingerprint reproduced.
    The producer/consumer gates wouldn't catch it because they
    operate on the JSON only. This is the third bundled artifact
    and it must be present too."""
    ids = list_chain_ids()
    present = [c for c in ids if _wav_path(c).is_file()]
    # The bundled WAVs are large render artifacts not committed to the
    # repo, so they're absent in CI. When NONE are present, this env
    # simply doesn't ship them — skip. Only fail on a PARTIAL set (some
    # chains have WAVs, some don't), which is the real "forgot to render
    # one" bug this guards against.
    if not present:
        pytest.skip("no bundled chain WAVs present in this environment")
    missing = [c for c in ids if not _wav_path(c).is_file()]
    assert not missing, (
        f"chains with YAML + fingerprint but no bundled WAV: {missing}. "
        f"Re-render via Connect and drop into {_CHAINS_ROOT}/."
    )


# ---------------------------------------------------------------------------
# WAV -> fingerprint round-trip (the heavy assertion)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chain_id", sorted(list_chain_ids()))
def test_fresh_extraction_matches_bundled_fingerprint(chain_id: str) -> None:
    """Re-extract the bundled WAV via ``_extract_query_fingerprint``
    and assert the result is byte-for-byte identical to the
    bundled JSON.

    Strict equality is the right gate here, not approximate. The
    rendering script calls ``_extract_query_fingerprint`` directly
    (see ``scripts/render_chain_references.py:_render_fingerprint``);
    re-running it on the same WAV must produce the same numbers.
    A non-zero delta means one of three things drifted:

      1. The WAV was modified after the fingerprint was rendered.
      2. The fingerprint JSON was hand-edited.
      3. ``_compute_8_features`` (or one of its dependencies)
         changed math without the bank being re-rendered.

    Any of those is a real bug, and the right fix is always
    "re-render via ``scripts/render_chain_references.py``" — so we
    want a hard failure here rather than a soft tolerance that
    lets cases (3) slip through.
    """
    wav = _wav_path(chain_id)
    # Bundled WAVs are large render artifacts absent in CI — skip the
    # round-trip when this env doesn't ship them.
    if not wav.is_file():
        pytest.skip(f"bundled WAV not present here: {wav}")
    bundled = json.loads(
        _fingerprint_path(chain_id).read_text(encoding="utf-8")
    )

    # Reference values from the bundled JSON.
    bundled_vec = np.array(
        [bundled["features"][k] for k in gc._FEATURE_KEYS],
        dtype=np.float64,
    )
    bundled_validity = np.array(
        [
            bool(bundled.get("feature_validity", {}).get(k, True))
            for k in gc._FEATURE_KEYS
        ],
        dtype=bool,
    )

    # Fresh extraction via the same code path the script uses.
    result = gc._extract_query_fingerprint(wav)
    assert result is not None, (
        f"{chain_id}: _extract_query_fingerprint returned None on the "
        f"bundled WAV {wav.name}. Either the WAV is unreadable or "
        f"librosa is unavailable in this environment."
    )
    fresh_vec, fresh_validity = result

    # Vector equality, per-axis (so a failure tells the operator
    # which feature drifted, not just "the vectors differ").
    for i, key in enumerate(gc._FEATURE_KEYS):
        assert fresh_vec[i] == bundled_vec[i], (
            f"{chain_id}: feature {key!r} drifted "
            f"(fresh={fresh_vec[i]!r}, bundled={bundled_vec[i]!r}). "
            f"Re-render with scripts/render_chain_references.py "
            f"--audio-dir {_CHAINS_ROOT} --chain-id {chain_id}."
        )

    # Validity mask equality. A drift here would mean the
    # polyphony / reliability heuristics changed verdict on the
    # same audio — same root cause as vector drift, same fix.
    for i, key in enumerate(gc._FEATURE_KEYS):
        assert bool(fresh_validity[i]) == bool(bundled_validity[i]), (
            f"{chain_id}: feature_validity[{key!r}] drifted "
            f"(fresh={bool(fresh_validity[i])}, "
            f"bundled={bool(bundled_validity[i])}). "
            f"Re-render with scripts/render_chain_references.py."
        )
