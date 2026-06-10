"""Render chain reference fingerprints.

Bridges *rendered chain reference audio* into the on-disk fingerprint
JSON files consumed by ``tone_forge.tone.guitar_catalog``. The catalog
ships with hand-authored placeholder fingerprints (see ``source`` /
``source_note`` fields in each ``<chain_id>.fingerprint.json``); this
script replaces those placeholders with measured features once the
operator has rendered reference WAVs.

Two-stage workflow
------------------

This script handles **stage 2 only**. Stage 1 (producing reference
WAVs) is a manual operator step because it requires either Connect or
Live, both of which are platform-coupled and out of scope for a Python
script.

Stage 1 — render reference audio (manual):

    For each chain id in the bank:
      1. Open Connect on the operator machine.
      2. Apply the chain (``connect.apply_chain`` with chain_id).
      3. Play the reference picking pattern (Plan §3.2 picking pattern
         B: open D + B chord, dampened, ~8s) through Connect.
      4. Bounce/record the output to a WAV file named
         ``<chain_id>.wav`` (e.g. ``tfc.ambient.wav``).
      5. Drop all WAVs into a single directory.

Stage 2 — extract fingerprints (this script):

    python3 -m scripts.render_chain_references \\
        --audio-dir ~/Desktop/chain_refs \\
        [--chain-id tfc.ambient]    # optional: restrict to one chain
        [--dry-run]                  # print, don't write
        [--out-dir <path>]           # default: tone_forge/monitor/chains

The script:
  * Reuses ``guitar_catalog._compute_8_features`` so the math is
    identical to the runtime query path. A divergence between catalog
    fingerprint math and query fingerprint math would silently
    re-introduce the calibration mismatch the z-norm step was added
    to fix.
  * Writes JSON with ``source="rendered_reference"`` and a
    ``rendered_at`` timestamp so the audit trail makes it clear the
    placeholder has been replaced.
  * Refuses to overwrite a measured fingerprint with a placeholder
    accidentally — but a measured fingerprint *can* be overwritten by
    another measured render (re-rendering after parameter tweak).

Boundary discipline
-------------------

* Reads chain YAMLs through ``tone_forge.monitor.loader`` so the
  script speaks the same shape as runtime.
* Does NOT call into Connect or any DAW. Audio rendering is out of
  scope; the script consumes whatever WAVs the operator drops on disk.
* Never deletes existing fingerprints. The placeholder vs measured
  distinction lives in the ``source`` field.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Allow `python3 scripts/render_chain_references.py` from backend/
_HERE = Path(__file__).resolve()
_BACKEND_ROOT = _HERE.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from tone_forge.monitor.loader import list_chain_ids, load_chain  # noqa: E402
from tone_forge.tone import guitar_catalog as gc  # noqa: E402

logger = logging.getLogger("render_chain_references")

# Source label written to fingerprints produced by this script.
SOURCE_RENDERED: str = "rendered_reference"
SOURCE_PLACEHOLDER: str = "hand_authored_estimate"

# WAV extensions accepted from the operator.
_WAV_EXTS: Tuple[str, ...] = (".wav", ".aif", ".aiff", ".flac")

DEFAULT_OUT_DIR: Path = _BACKEND_ROOT / "tone_forge" / "monitor" / "chains"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render_chain_references",
        description=(
            "Extract 8-feature fingerprints from rendered chain reference "
            "WAVs and write them next to the chain YAMLs."
        ),
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        required=True,
        help=(
            "Directory containing one rendered WAV per chain id, named "
            "'<chain_id>.wav' (e.g. tfc.ambient.wav)."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=(
            "Where to write '<chain_id>.fingerprint.json' (default: "
            "next to the chain YAMLs)."
        ),
    )
    parser.add_argument(
        "--chain-id",
        type=str,
        action="append",
        default=None,
        help=(
            "Restrict to specific chain id(s). May be repeated. "
            "Default: every chain in the bank."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching disk.",
    )
    parser.add_argument(
        "--allow-placeholder-overwrite",
        action="store_true",
        help=(
            "Permit overwriting a JSON whose 'source' is "
            f"{SOURCE_PLACEHOLDER!r}. Default is to overwrite anyway — "
            "this flag is here for future cases where placeholders "
            "should be sticky."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _find_audio_for_chain(
    audio_dir: Path, chain_id: str
) -> Optional[Path]:
    """Resolve ``<chain_id>.<ext>`` for any of the accepted extensions.

    Returns ``None`` (caller logs) instead of raising so a partial run
    over multiple chains can report all gaps in one pass.
    """
    for ext in _WAV_EXTS:
        candidate = audio_dir / f"{chain_id}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _read_existing_source(out_path: Path) -> Optional[str]:
    if not out_path.is_file():
        return None
    try:
        existing = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    source = existing.get("source") if isinstance(existing, dict) else None
    return str(source) if isinstance(source, str) else None


def _render_fingerprint(
    chain_id: str, display_name: str, family: str, audio_path: Path,
) -> Optional[dict]:
    """Compute the 8-feature vector and return the on-disk JSON shape.

    Uses ``guitar_catalog._extract_query_fingerprint`` so the runtime
    query path and the catalog reference path produce identical
    numbers from the same audio. Returns ``None`` on extraction
    failure (caller logs and skips that chain).

    Writes both the feature vector and a ``feature_validity`` mask so
    the catalog row carries the same trust signal that queries carry.
    Reference chains rendered from clean monophonic DI should all be
    True, but we capture whatever the extractor reported rather than
    hard-coding the assumption.

    ``display_name`` and ``family`` are baked into the JSON so the
    runtime catalog can read them without crossing the tone->monitor
    import boundary (see commit c6ff8d1).
    """
    result = gc._extract_query_fingerprint(audio_path)
    if result is None:
        return None
    vector, validity = result

    features = {
        key: float(vector[i]) for i, key in enumerate(gc._FEATURE_KEYS)
    }
    feature_validity = {
        key: bool(validity[i]) for i, key in enumerate(gc._FEATURE_KEYS)
    }

    return {
        "chain_id": chain_id,
        "display_name": display_name,
        "family": family,
        "source": SOURCE_RENDERED,
        "source_note": (
            "Measured by scripts/render_chain_references.py from "
            f"{audio_path.name}. Replaces hand-authored placeholder. "
            "Re-render and re-run this script after any chain "
            "parameter change."
        ),
        "rendered_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "rendered_from": audio_path.name,
        "features": features,
        "feature_validity": feature_validity,
    }


def _resolve_targets(
    requested: Optional[Iterable[str]],
) -> List[str]:
    """Return the chain ids to process, validating against the bank.

    Raises ``SystemExit`` for unknown ids — running silently against a
    subset would mask typos.
    """
    available = list_chain_ids()
    if requested is None:
        return available
    requested_list = list(requested)
    unknown = [cid for cid in requested_list if cid not in available]
    if unknown:
        raise SystemExit(
            f"Unknown chain id(s): {unknown}. "
            f"Available: {available}"
        )
    return requested_list


def render(
    *,
    audio_dir: Path,
    out_dir: Path,
    chain_ids: Optional[Iterable[str]] = None,
    dry_run: bool = False,
    allow_placeholder_overwrite: bool = True,
) -> int:
    """Render fingerprints for the requested chains.

    Returns process exit code: 0 on full success, 1 if any chain
    could not be processed.
    """
    if not audio_dir.is_dir():
        logger.error("audio-dir does not exist: %s", audio_dir)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = _resolve_targets(chain_ids)
    if not targets:
        logger.error("No chain ids to process — bank is empty.")
        return 1

    failed: List[str] = []
    for chain_id in targets:
        # Resolve display_name early so log lines are self-describing.
        try:
            chain = load_chain(chain_id)
        except Exception as exc:
            logger.error("Failed to load chain YAML for %s: %s", chain_id, exc)
            failed.append(chain_id)
            continue

        audio_path = _find_audio_for_chain(audio_dir, chain_id)
        if audio_path is None:
            logger.warning(
                "No reference WAV for %s in %s (expected '<chain_id><ext>' "
                "with ext in %s). Skipping.",
                chain_id, audio_dir, _WAV_EXTS,
            )
            failed.append(chain_id)
            continue

        out_path = out_dir / f"{chain_id}.fingerprint.json"
        existing_source = _read_existing_source(out_path)
        if (
            existing_source == SOURCE_PLACEHOLDER
            and not allow_placeholder_overwrite
        ):
            logger.info(
                "Skipping %s — existing fingerprint is %r and "
                "--allow-placeholder-overwrite was not set.",
                chain_id, existing_source,
            )
            continue

        logger.info(
            "Rendering %s (%s) from %s",
            chain_id, chain.display_name, audio_path.name,
        )
        payload = _render_fingerprint(
            chain_id, chain.display_name, chain.family.value, audio_path,
        )
        if payload is None:
            logger.error("Feature extraction failed for %s.", chain_id)
            failed.append(chain_id)
            continue

        if dry_run:
            print(json.dumps(payload, indent=2))
            continue

        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        logger.info("Wrote %s", out_path)

    if failed:
        logger.error("Failed chains: %s", failed)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Reset the cache so a re-run inside the same Python process picks
    # up fingerprints we just wrote. Useful for downstream tests.
    gc._reset_catalog_cache()
    return render(
        audio_dir=args.audio_dir,
        out_dir=args.out_dir,
        chain_ids=args.chain_id,
        dry_run=args.dry_run,
        allow_placeholder_overwrite=args.allow_placeholder_overwrite,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
