#!/usr/bin/env python3
"""Simple script to open the next unrendered preset in Ableton.

Run this, then manually export (Cmd+Shift+R) and save to the audio folder.
Run again to open the next one.
"""
import subprocess
import sys
from pathlib import Path

ALS_DIR = Path(__file__).parent.parent / "preset_catalog_output" / "als"
AUDIO_DIR = Path(__file__).parent.parent / "preset_catalog_output" / "audio"

def get_pending():
    """Get list of ALS files without corresponding WAV."""
    als_files = sorted(ALS_DIR.glob("*.als"))
    pending = []
    for als in als_files:
        wav = AUDIO_DIR / (als.stem + ".wav")
        if not wav.exists():
            pending.append(als)
    return pending

def main():
    AUDIO_DIR.mkdir(exist_ok=True)
    pending = get_pending()

    if not pending:
        print("All presets rendered!")
        return

    next_file = pending[0]
    remaining = len(pending)

    print(f"Opening: {next_file.name}")
    print(f"Remaining: {remaining} presets")
    print(f"\nExport to: {AUDIO_DIR / (next_file.stem + '.wav')}")
    print("\n1. Press Cmd+Shift+R in Ableton")
    print("2. Navigate to preset_catalog_output/audio/")
    print(f"3. Save as: {next_file.stem}.wav")
    print("4. Run this script again for the next preset")

    subprocess.run(["open", str(next_file)])

if __name__ == "__main__":
    main()
