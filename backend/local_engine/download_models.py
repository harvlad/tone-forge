#!/usr/bin/env python3
"""
ToneForge Model Downloader

Downloads required ML models for stem separation and MIDI extraction.
Run this once after installation to pre-cache models.

Usage:
    python download_models.py [--force]
"""

import sys
import os
from pathlib import Path

def get_cache_dir():
    """Get the torch hub cache directory."""
    return Path.home() / ".cache" / "torch" / "hub" / "checkpoints"

def check_model_exists(model_hash: str) -> bool:
    """Check if a model is already cached."""
    cache_dir = get_cache_dir()
    # Demucs saves models as {hash}-{secondary_hash}.th
    for f in cache_dir.glob(f"{model_hash}*.th"):
        return True
    return False

def download_demucs_models(force: bool = False):
    """Download Demucs stem separation models."""
    print("\n=== Downloading Demucs Stem Separation Models ===\n")

    models = {
        "htdemucs": {
            "hash": "955717e8",
            "description": "4-stem model (drums, bass, other, vocals)",
            "size": "~80 MB",
        },
        "htdemucs_6s": {
            "hash": "5c90dfd2",
            "description": "6-stem model (drums, bass, guitar, piano, vocals, other)",
            "size": "~80 MB",
        },
    }

    try:
        from demucs.pretrained import get_model
        import torch
    except ImportError:
        print("ERROR: demucs not installed. Run: pip install demucs")
        return False

    for name, info in models.items():
        if not force and check_model_exists(info["hash"]):
            print(f"  [CACHED] {name}: {info['description']}")
            continue

        print(f"  Downloading {name}: {info['description']} ({info['size']})...")
        try:
            model = get_model(name)
            print(f"  [OK] {name} downloaded successfully")
        except Exception as e:
            print(f"  [ERROR] Failed to download {name}: {e}")
            return False

    return True

def download_basic_pitch_model(force: bool = False):
    """Download Basic Pitch MIDI extraction model."""
    print("\n=== Downloading Basic Pitch MIDI Model ===\n")

    try:
        import basic_pitch
        from basic_pitch.inference import predict
        from basic_pitch import ICASSP_2022_MODEL_PATH

        model_path = Path(ICASSP_2022_MODEL_PATH)
        if model_path.exists() and not force:
            print(f"  [CACHED] Basic Pitch model at {model_path}")
            return True

        print("  Downloading Basic Pitch model (~50 MB)...")
        # Trigger model download by importing
        from basic_pitch.inference import Model
        print("  [OK] Basic Pitch model ready")
        return True

    except ImportError:
        print("  [SKIP] basic_pitch not installed (optional for MIDI extraction)")
        return True
    except Exception as e:
        print(f"  [WARN] Basic Pitch setup issue: {e}")
        return True  # Non-critical

def download_beat_this_model(force: bool = False):
    """Download Beat This! beat/downbeat checkpoint (~78 MB)."""
    print("\n=== Downloading Beat This! Beat Tracker ===\n")

    try:
        from beat_this.inference import Audio2Beats

        # Instantiating triggers the checkpoint download to the
        # beat_this cache dir; harmless no-op when already cached.
        Audio2Beats(checkpoint_path="final0", device="cpu", dbn=False)
        print("  [OK] Beat This! checkpoint ready")
        return True
    except ImportError:
        print("  [SKIP] beat_this not installed "
              "(librosa fallback will be used)")
        return True
    except Exception as e:
        print(f"  [WARN] Beat This! setup issue: {e}")
        return True  # Non-critical: tracker falls back to librosa


def download_allin1_model(force: bool = False):
    """Download All-In-One structure model checkpoints (~11 MB)."""
    print("\n=== Downloading All-In-One Structure Model ===\n")
    try:
        from allin1.models import load_pretrained_model
        # Loading triggers the Hugging Face checkpoint downloads
        # (taejunkim/allinone, 8 fold checkpoints, ~11 MB total);
        # harmless no-op when already cached.
        load_pretrained_model(model_name="harmonix-all", device="cpu")
        print("  [OK] All-In-One structure model ready")
        return True
    except ImportError:
        print("  [SKIP] allin1 not installed "
              "(RMS-novelty section fallback will be used)")
        return True
    except Exception as e:
        print(f"  [WARN] All-In-One setup issue: {e}")
        return True  # Non-critical: sections fall back to RMS novelty


def show_cache_status():
    """Show what's currently cached."""
    print("\n=== Cache Status ===\n")

    cache_dir = get_cache_dir()
    if not cache_dir.exists():
        print(f"  Cache directory not found: {cache_dir}")
        return

    total_size = 0
    for f in cache_dir.iterdir():
        if f.is_file():
            size = f.stat().st_size
            total_size += size
            print(f"  {f.name}: {size / 1024 / 1024:.1f} MB")

    print(f"\n  Total cached: {total_size / 1024 / 1024:.1f} MB")
    print(f"  Location: {cache_dir}")

def main():
    force = "--force" in sys.argv

    print("=" * 60)
    print("  ToneForge Model Downloader")
    print("=" * 60)

    if force:
        print("\n  --force flag set: re-downloading all models\n")

    # Download models
    demucs_ok = download_demucs_models(force)
    pitch_ok = download_basic_pitch_model(force)
    download_beat_this_model(force)
    download_allin1_model(force)

    # Show status
    show_cache_status()

    print("\n" + "=" * 60)
    if demucs_ok and pitch_ok:
        print("  All models downloaded successfully!")
        print("  ToneForge is ready for deep analysis.")
    else:
        print("  Some models failed to download.")
        print("  Deep analysis may not work until resolved.")
    print("=" * 60 + "\n")

    return 0 if (demucs_ok and pitch_ok) else 1

if __name__ == "__main__":
    sys.exit(main())
