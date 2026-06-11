"""Extract CLAP (or OpenL3 fallback) embeddings for the Analog preset catalog.

Saves a single ``learned_embeddings.npz`` containing:
    embeddings:   (N, 512) float32
    preset_ids:   (N,)    str
    encoder_name: str  ("clap" | "openl3")
    sample_rate:  int  (48000 for CLAP / OpenL3)

This is a one-time extraction job. The output is consumed by
``representation_experiments_v2.py``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Tuple

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Quiet HuggingFace noise.
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("clap_embed")

CATALOG_PATH = (
    BACKEND_DIR / "preset_catalog_output" / "catalog" / "catalog_analog.json"
)
OUT_PATH = (
    BACKEND_DIR / "preset_catalog_output" / "retrieval" / "learned_embeddings.npz"
)


def try_load_clap():
    try:
        import laion_clap  # noqa: F401
    except Exception as e:
        log.info("CLAP not importable: %s", e)
        return None
    try:
        m = laion_clap.CLAP_Module(enable_fusion=False)
        log.info("loading CLAP checkpoint...")
        m.load_ckpt()
        return ("clap", m)
    except Exception as e:
        log.warning("CLAP failed to load: %s", e)
        return None


def try_load_openl3():
    try:
        import openl3  # noqa: F401
    except Exception as e:
        log.info("OpenL3 not importable: %s", e)
        return None
    return ("openl3", None)


def encode_one_clap(model, audio: np.ndarray) -> np.ndarray:
    emb = model.get_audio_embedding_from_data(
        x=audio[np.newaxis, :], use_tensor=False,
    )
    return emb[0].astype(np.float32)


def encode_one_openl3(audio: np.ndarray, sr: int) -> np.ndarray:
    import openl3
    emb, _ts = openl3.get_audio_embedding(
        audio, sr,
        content_type="music",
        input_repr="mel256",
        embedding_size=512,
    )
    return np.mean(emb, axis=0).astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--max-seconds", type=float, default=8.0)
    ap.add_argument(
        "--prefer",
        choices=["clap", "openl3"],
        default="clap",
        help="Which encoder to try first.",
    )
    args = ap.parse_args()

    import librosa

    presets = json.loads(args.catalog.read_text())["presets"]
    log.info("loaded %d presets", len(presets))

    # Pick encoder
    encoder = None
    if args.prefer == "clap":
        encoder = try_load_clap() or try_load_openl3()
    else:
        encoder = try_load_openl3() or try_load_clap()
    if encoder is None:
        log.error("no learned encoder available (install laion-clap or openl3)")
        return 2
    encoder_name, model = encoder
    sample_rate = 48000  # both CLAP and OpenL3 expect 48k
    log.info("encoder=%s, sample_rate=%d", encoder_name, sample_rate)

    embeddings: List[np.ndarray] = []
    kept_ids: List[str] = []
    t0 = time.time()
    for i, p in enumerate(presets):
        ap_path = p.get("audio_path")
        if not ap_path or not Path(ap_path).exists():
            log.warning("missing audio for %s", p.get("preset_id"))
            continue
        try:
            y, _ = librosa.load(
                ap_path, sr=sample_rate, mono=True, duration=args.max_seconds,
            )
            if y.size == 0:
                continue
            if encoder_name == "clap":
                emb = encode_one_clap(model, y)
            else:
                emb = encode_one_openl3(y, sample_rate)
            embeddings.append(emb)
            kept_ids.append(p["preset_id"])
            if (i + 1) % 10 == 0:
                log.info("encoded %d/%d (%.1fs)",
                         i + 1, len(presets), time.time() - t0)
        except Exception as e:
            log.warning("encode failed for %s: %s", p.get("preset_id"), e)

    if not embeddings:
        log.error("no embeddings produced")
        return 3

    X = np.stack(embeddings, axis=0).astype(np.float32)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        args.out,
        embeddings=X,
        preset_ids=np.array(kept_ids),
        encoder_name=np.array(encoder_name),
        sample_rate=np.array(sample_rate),
    )
    log.info("wrote %s shape=%s in %.1fs", args.out, X.shape, time.time() - t0)
    print(json.dumps({
        "encoder": encoder_name,
        "n_presets": len(kept_ids),
        "embedding_dim": int(X.shape[1]),
        "output": str(args.out),
        "elapsed_seconds": round(time.time() - t0, 1),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
