#!/usr/bin/env python3
"""Open the next preset that needs exporting.

Run this, export in Ableton (Cmd+Shift+R, Enter), then run again.
Files export to Desktop - we'll move them all at the end.
"""
import subprocess
from pathlib import Path

ALS_DIR = Path(__file__).parent.parent / "preset_catalog_output" / "als"
AUDIO_DIR = Path(__file__).parent.parent / "preset_catalog_output" / "audio"

def get_pending():
    als_files = sorted(ALS_DIR.glob("*.als"))
    return [a for a in als_files if not (AUDIO_DIR / (a.stem + ".wav")).exists()]

def main():
    pending = get_pending()

    if not pending:
        print("All done! Run: python scripts/move_from_desktop.py")
        return

    next_file = pending[0]
    print(f"[{99 - len(pending) + 1}/99] Opening: {next_file.stem}")
    print(f"Remaining: {len(pending)}")
    print("\n→ Press Cmd+Shift+R, then Enter to export")

    subprocess.run(["open", str(next_file)])

if __name__ == "__main__":
    main()
