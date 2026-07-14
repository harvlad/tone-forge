"""All-In-One music structure backend (task 11).

Runs the All-In-One structure model (Kim & Nam, WASPAA 2023;
``all-in-one-mps`` port, MIT) to get section boundaries and
functional labels (verse/chorus/bridge/...). Replaces the RMS-novelty
Stage 0 of ``SectionDetector`` when available.

Measured on 24 SALAMI Internet-Archive tracks (function annotations,
``scripts.section_eval``):

    boundary F@0.5s   0.060 -> 0.404
    boundary F@3.0s   0.298 -> 0.565
    label accuracy    0.232 -> 0.495

Optional at runtime: when ``allin1`` isn't importable (or inference
fails) callers fall back to the RMS-novelty ``SectionDetector`` path,
mirroring the ``tone_forge.beat_tracking`` degradation pattern.

Stem reuse: the pipeline already runs Demucs (htdemucs) for stem
separation. ``analyze_structure`` accepts those stem paths and stages
them into allin1's expected demix layout so the model skips its own
Demucs pass — structure inference then costs only the transformer
forward (~41 s CPU / ~7 s MPS for a 4-minute song).
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

__all__ = ["analyze_structure", "allin1_available"]

# Process-level failure latch: import/inference environment problems
# don't retry per song.
_ALLIN1_FAILED = False

# Demucs 4-stem names allin1's spectrogram stage expects on disk.
_CORE_STEMS = ("bass", "drums", "other", "vocals")

# htdemucs_6s extras that fold into "other" for the 4-stem layout.
_OTHER_EXTRAS = ("guitar", "piano")


def allin1_available() -> bool:
    """True when the allin1 package imports and hasn't failed before."""
    global _ALLIN1_FAILED
    if _ALLIN1_FAILED:
        return False
    try:
        import allin1  # noqa: F401
        return True
    except Exception:
        _ALLIN1_FAILED = True
        return False


def _pick_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _stage_stems(
    stems: Mapping[str, Any],
    mix_stem_name: str,
    demix_dir: Path,
) -> bool:
    """Stage pipeline stems into allin1's demix layout.

    Layout: ``demix_dir/htdemucs/<mix_stem_name>/{bass,drums,other,
    vocals}.wav``. Symlinks when the stem maps 1:1; htdemucs_6s
    guitar/piano stems are summed into other.wav. Returns False when
    the stem set is incomplete (caller lets allin1 demix itself).
    """
    paths: Dict[str, Path] = {}
    for name, p in stems.items():
        try:
            path = Path(p)
        except TypeError:
            return False
        if path.is_file():
            paths[name] = path
    if not all(name in paths for name in _CORE_STEMS):
        return False

    out_dir = demix_dir / "htdemucs" / mix_stem_name
    out_dir.mkdir(parents=True, exist_ok=True)

    extras = [paths[n] for n in _OTHER_EXTRAS if n in paths]
    for name in _CORE_STEMS:
        dest = out_dir / f"{name}.wav"
        if name == "other" and extras:
            _write_summed(paths["other"], extras, dest)
        else:
            dest.symlink_to(paths[name].resolve())
    return True


def _write_summed(base: Path, extras: List[Path], dest: Path) -> None:
    """other.wav = other + guitar + piano (htdemucs_6s -> 4-stem)."""
    import numpy as np
    import soundfile as sf

    audio, sr = sf.read(str(base), always_2d=True)
    for extra in extras:
        y, sr_e = sf.read(str(extra), always_2d=True)
        if sr_e != sr:
            continue  # mismatched extra adds nothing; skip it
        n = min(len(audio), len(y))
        audio = audio[:n] + y[:n]
    audio = np.clip(audio, -1.0, 1.0)
    sf.write(str(dest), audio, sr, subtype="PCM_16")


def analyze_structure(
    mix_path: Any,
    stems: Optional[Mapping[str, Any]] = None,
    device: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Run All-In-One structure analysis on a full mix.

    Args:
        mix_path: Path to the mix audio file (any ffmpeg-decodable
            format).
        stems: Optional mapping of demucs stem name -> path
            (drums/bass/other/vocals, plus guitar/piano from
            htdemucs_6s). When the four core stems are present the
            model's own Demucs pass is skipped.
        device: torch device override; auto (mps > cuda > cpu) when
            None.

    Returns:
        ``{"segments": [{"start", "end", "label"}, ...], "bpm": float
        | None}`` with labels from the Harmonix vocabulary (start/end/
        intro/outro/break/bridge/inst/solo/verse/chorus), or ``None``
        when allin1 is unavailable or inference fails.
    """
    global _ALLIN1_FAILED
    if not allin1_available():
        return None

    mix = Path(mix_path)
    if not mix.is_file():
        return None

    device = device or _pick_device()
    tmp = Path(tempfile.mkdtemp(prefix="tf_structure_"))
    try:
        import allin1

        demix_dir = tmp / "demix"
        spec_dir = tmp / "spec"
        demix_dir.mkdir(parents=True)
        spec_dir.mkdir(parents=True)

        staged = False
        if stems:
            try:
                staged = _stage_stems(stems, mix.stem, demix_dir)
            except Exception as e:
                logger.warning(f"Stem staging for structure failed: {e}")
        if not staged:
            logger.info(
                "Structure backend running its own demix "
                "(pipeline stems unavailable)"
            )

        result = allin1.analyze(
            str(mix),
            device=device,
            demucs_device=device,
            demix_dir=str(demix_dir),
            spec_dir=str(spec_dir),
            keep_byproducts=True,  # keep our tmp layout; we clean up
        )
        segments = [
            {
                "start": float(s.start),
                "end": float(s.end),
                "label": str(s.label),
            }
            for s in result.segments
        ]
        if not segments:
            return None
        bpm = float(result.bpm) if result.bpm else None
        logger.info(
            f"allin1 structure: {len(segments)} segments on {device}"
        )
        return {"segments": segments, "bpm": bpm}
    except Exception as e:
        # Inference-environment failures (missing weights offline,
        # torch/natten incompatibility) latch; corrupt-input failures
        # would too, but the fallback detector still runs for them.
        logger.warning(f"allin1 structure analysis failed: {e}")
        _ALLIN1_FAILED = True
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
