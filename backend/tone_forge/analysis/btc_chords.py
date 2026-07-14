"""BTC chord recognition adapter — array in, tone-forge regions out.

Wraps the vendored BTC-ISMIR19 transformer (``vendor.btc_ismir19``,
MIT) behind the same call shape the corpus scoreboard uses for the
in-house detector:

    regions = detect_chords_btc(y, sr)   # [{"start","end","label"}]

Feature extraction reproduces upstream ``audio_file_to_features``
exactly (22050 Hz mono, 10-s chunked CQT, n_bins=144, 24 bins/octave,
hop 2048, log magnitude) so the pretrained checkpoints see the same
distribution they were trained on. Frame time uses the upstream
convention of ``inst_len / timestep`` seconds per frame within each
10-s chunk.

Two checkpoints ship with the vendor drop:

* ``majmin``     — 25 classes (12 roots x maj/min + N)
* ``large_voca`` — 170 classes (12 roots x 14 qualities + N + X)

Labels are mapped from mir_eval syntax ("C:min7") to the tone-forge
symbol convention ("Cm7") that ``chord_eval.normalise_symbol``
parses; qualities outside the tone-forge vocabulary collapse to
their triad family (min6 -> m, hdim7 -> dim, maj6 -> maj). "N"
(no chord) and "X" (unknown) emit no region — uncovered time simply
scores zero under the WCSR duration denominator.

Model + checkpoint are cached per (vocab, device) so repeated calls
(corpus loops) pay the ~12 MB load once.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


__all__ = ["detect_chords_btc", "btc_label_to_symbol"]


_CHECKPOINT_DIR = (
    Path(__file__).resolve().parent.parent.parent / "vendor" / "btc_ismir19" / "checkpoints"
)

# Upstream run_config.yaml, model section (feature/mp3 constants inline
# below). num_chords differs per checkpoint.
_MODEL_CONFIG_BASE: Dict[str, Any] = {
    "feature_size": 144,
    "timestep": 108,
    "input_dropout": 0.2,
    "layer_dropout": 0.2,
    "attention_dropout": 0.2,
    "relu_dropout": 0.2,
    "num_layers": 8,
    "num_heads": 4,
    "hidden_size": 128,
    "total_key_depth": 128,
    "total_value_depth": 128,
    "filter_size": 128,
    "probs_out": False,
}

_VOCABS = {
    "majmin": {"num_chords": 25, "checkpoint": "btc_model.pt"},
    "large_voca": {"num_chords": 170, "checkpoint": "btc_model_large_voca.pt"},
}

_SONG_HZ = 22050
_INST_LEN_S = 10.0
_N_BINS = 144
_BINS_PER_OCTAVE = 24
_HOP_LENGTH = 2048
_TIMESTEP = 108

_ROOTS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_QUALITIES = [
    "min", "maj", "dim", "aug", "min6", "maj6", "min7",
    "minmaj7", "maj7", "7", "dim7", "hdim7", "sus2", "sus4",
]

# mir_eval quality -> tone-forge suffix (chord_eval._SYMBOL_RE vocab).
_QUALITY_TO_SUFFIX = {
    "maj": "",
    "min": "m",
    "dim": "dim",
    "aug": "aug",
    "min6": "m",        # collapse: no 6th chords in tone-forge vocab
    "maj6": "",
    "min7": "m7",
    "minmaj7": "m",     # collapse to minor triad family
    "maj7": "maj7",
    "7": "7",
    "dim7": "dim7",
    "hdim7": "dim",     # half-diminished collapses to dim family
    "sus2": "sus2",
    "sus4": "sus4",
}


def _idx_to_label(vocab: str) -> List[str]:
    """Class index -> mir_eval label, mirroring upstream mappings."""
    if vocab == "majmin":
        labels = []
        for root in _ROOTS:
            labels.append(root)
            labels.append(f"{root}:min")
        labels.append("N")
        return labels
    labels = [""] * 170
    labels[169] = "N"
    labels[168] = "X"
    for i in range(168):
        root = _ROOTS[i // 14]
        quality = _QUALITIES[i % 14]
        labels[i] = root if quality == "maj" else f"{root}:{quality}"
    return labels


def btc_label_to_symbol(label: str) -> Optional[str]:
    """Map a BTC mir_eval label to a tone-forge chord symbol.

    "C" -> "C", "C:min7" -> "Cm7", "F#:hdim7" -> "F#dim".
    Returns None for "N" / "X" (no region should be emitted).
    """
    if label in ("N", "X", ""):
        return None
    if ":" not in label:
        return label
    root, quality = label.split(":", 1)
    suffix = _QUALITY_TO_SUFFIX.get(quality)
    if suffix is None:
        return root  # unknown quality: fall back to major triad root
    return f"{root}{suffix}"


# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------

_MODEL_CACHE: Dict[Tuple[str, str], Tuple[Any, float, float]] = {}


def _default_device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def _load_model(vocab: str, device: str):
    """Load (model, mean, std) once per (vocab, device)."""
    key = (vocab, device)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    import torch

    from vendor.btc_ismir19 import BTC_model

    spec = _VOCABS[vocab]
    config = dict(_MODEL_CONFIG_BASE, num_chords=spec["num_chords"])
    model = BTC_model(config=config).to(device)
    ckpt_path = _CHECKPOINT_DIR / spec["checkpoint"]
    # Trusted vendored checkpoint pinned by commit; contains numpy
    # mean/std scalars, so weights_only loading is not possible.
    checkpoint = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    model.load_state_dict(checkpoint["model"])
    model.eval()
    mean = float(checkpoint["mean"])
    std = float(checkpoint["std"])
    _MODEL_CACHE[key] = (model, mean, std)
    return _MODEL_CACHE[key]


# ---------------------------------------------------------------------------
# Feature extraction (upstream parity)
# ---------------------------------------------------------------------------


def _cqt_features(y, sr: int):
    """Chunked log-CQT identical to upstream audio_file_to_features."""
    import librosa
    import numpy as np

    if sr != _SONG_HZ:
        y = librosa.resample(y, orig_sr=sr, target_sr=_SONG_HZ)
    chunk = int(_SONG_HZ * _INST_LEN_S)
    pieces = []
    idx = 0
    while len(y) > idx + chunk:
        pieces.append(
            librosa.cqt(
                y[idx : idx + chunk], sr=_SONG_HZ, n_bins=_N_BINS,
                bins_per_octave=_BINS_PER_OCTAVE, hop_length=_HOP_LENGTH,
            )
        )
        idx += chunk
    tail = y[idx:]
    if len(tail):
        pieces.append(
            librosa.cqt(
                tail, sr=_SONG_HZ, n_bins=_N_BINS,
                bins_per_octave=_BINS_PER_OCTAVE, hop_length=_HOP_LENGTH,
            )
        )
    feature = np.concatenate(pieces, axis=1)
    return np.log(np.abs(feature) + 1e-6)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def detect_chords_btc(
    y,
    sr: int,
    vocab: str = "large_voca",
    device: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run BTC on an audio array; return chord regions.

    Output: sorted ``[{"start": s, "end": s, "label": tone-forge
    symbol}]``. N/X frames produce gaps rather than regions.
    """
    import numpy as np
    import torch

    if vocab not in _VOCABS:
        raise ValueError(f"unknown BTC vocab {vocab!r}; use majmin|large_voca")
    device = device or _default_device()
    model, mean, std = _load_model(vocab, device)
    idx_to_label = _idx_to_label(vocab)

    feature = _cqt_features(y, sr).T  # (frames, 144)
    feature = (feature - mean) / std
    n_frames = feature.shape[0]
    num_pad = _TIMESTEP - (n_frames % _TIMESTEP)
    feature = np.pad(feature, ((0, num_pad), (0, 0)), mode="constant")
    num_instance = feature.shape[0] // _TIMESTEP
    time_unit = _INST_LEN_S / _TIMESTEP  # upstream frame-time convention

    preds: List[int] = []
    with torch.no_grad():
        tens = torch.tensor(feature, dtype=torch.float32).unsqueeze(0).to(device)
        for t in range(num_instance):
            encoded, _ = model.self_attn_layers(
                tens[:, _TIMESTEP * t : _TIMESTEP * (t + 1), :]
            )
            prediction, _second = model.output_layer(encoded)
            preds.extend(int(v) for v in prediction.squeeze(0).cpu().tolist())
    preds = preds[:n_frames]  # drop padding frames

    regions: List[Dict[str, Any]] = []
    if not preds:
        return regions
    start = 0.0
    prev = preds[0]
    for i in range(1, len(preds)):
        if preds[i] != prev:
            symbol = btc_label_to_symbol(idx_to_label[prev])
            if symbol is not None:
                regions.append(
                    {"start": start, "end": i * time_unit, "label": symbol}
                )
            start = i * time_unit
            prev = preds[i]
    symbol = btc_label_to_symbol(idx_to_label[prev])
    if symbol is not None:
        regions.append(
            {"start": start, "end": len(preds) * time_unit, "label": symbol}
        )
    return regions
