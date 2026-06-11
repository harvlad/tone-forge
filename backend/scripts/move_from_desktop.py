#!/usr/bin/env python3
"""Move exported WAV files from Desktop to audio folder."""
import shutil
from pathlib import Path

AUDIO_DIR = Path(__file__).parent.parent / "preset_catalog_output" / "audio"
DESKTOP = Path.home() / "Desktop"

def main():
    AUDIO_DIR.mkdir(exist_ok=True)

    # Find matching files
    patterns = ["analog_*.wav", "synth_essentials_*.wav"]
    moved = 0

    for pattern in patterns:
        for src in DESKTOP.glob(pattern):
            dst = AUDIO_DIR / src.name
            if not dst.exists():
                shutil.move(str(src), str(dst))
                print(f"Moved: {src.name}")
                moved += 1

                # Clean up .asd and .mp3
                for ext in [".mp3", ".wav.asd", ".mp3.asd"]:
                    extra = DESKTOP / (src.stem + ext)
                    if extra.exists():
                        extra.unlink()

    print(f"\nMoved {moved} files to {AUDIO_DIR}")
    print(f"Total files: {len(list(AUDIO_DIR.glob('*.wav')))}/99")

if __name__ == "__main__":
    main()
