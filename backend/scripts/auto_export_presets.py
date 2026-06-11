#!/usr/bin/env python3
"""Automated preset export using PyAutoGUI.

Exports to Desktop (default), then moves files to target directory.

Usage:
    python scripts/auto_export_presets.py [--test] [--start N]
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pyautogui

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1

# Paths
ALS_DIR = Path(__file__).parent.parent / "preset_catalog_output" / "als"
AUDIO_DIR = Path(__file__).parent.parent / "preset_catalog_output" / "audio"
DESKTOP = Path.home() / "Desktop"

# Timing (longer for reliability)
WAIT_AFTER_OPEN = 6.0
WAIT_FOR_DIALOG = 2.5
WAIT_FOR_EXPORT = 25.0  # Export takes time


def get_pending_presets():
    """Get ALS files that don't have corresponding WAV."""
    als_files = sorted(ALS_DIR.glob("*.als"))
    pending = []
    for als in als_files:
        wav = AUDIO_DIR / (als.stem + ".wav")
        if not wav.exists():
            pending.append(als)
    return pending


def activate_ableton():
    """Bring Ableton to foreground."""
    subprocess.run(["osascript", "-e",
        'tell application "Ableton Live 12 Standard" to activate'],
        capture_output=True)
    time.sleep(0.5)


def open_als_file(als_path: Path):
    """Open an ALS file."""
    subprocess.run(["open", str(als_path)], capture_output=True)
    time.sleep(WAIT_AFTER_OPEN)
    activate_ableton()


def export_current_project(filename: str):
    """Export current project to Desktop."""
    # Open export dialog
    pyautogui.hotkey('command', 'shift', 'r')
    time.sleep(WAIT_FOR_DIALOG)

    # Clear filename and type new one
    pyautogui.hotkey('command', 'a')
    time.sleep(0.1)
    pyautogui.typewrite(filename.replace('.wav', ''), interval=0.008)
    time.sleep(0.3)

    # Press Enter to export
    pyautogui.press('return')
    time.sleep(WAIT_FOR_EXPORT)


def move_exported_file(filename: str):
    """Move exported file from Desktop to target directory."""
    src_wav = DESKTOP / filename
    src_mp3 = DESKTOP / filename.replace('.wav', '.mp3')

    # Wait for file to appear (up to 30 seconds)
    for _ in range(30):
        if src_wav.exists():
            time.sleep(2)  # Extra time for file to finish writing
            break
        time.sleep(1)

    if src_wav.exists():
        dst = AUDIO_DIR / filename
        shutil.move(str(src_wav), str(dst))
        print(f"    Moved: {filename}")

        # Clean up MP3 and .asd files
        if src_mp3.exists():
            src_mp3.unlink()
        for asd in DESKTOP.glob(f"{filename.replace('.wav', '')}*.asd"):
            asd.unlink()
        return True
    return False


def close_current_project():
    """Close current project without saving."""
    pyautogui.hotkey('command', 'w')
    time.sleep(0.5)
    # Don't Save
    pyautogui.hotkey('command', 'd')
    time.sleep(0.5)


def export_single_preset(als_path: Path, idx: int, total: int):
    """Export a single preset."""
    wav_name = als_path.stem + ".wav"

    print(f"[{idx}/{total}] {als_path.stem}")

    open_als_file(als_path)
    export_current_project(wav_name)

    if move_exported_file(wav_name):
        close_current_project()
        return True
    else:
        print(f"    WARNING: File not found on Desktop")
        return False


def main():
    parser = argparse.ArgumentParser(description="Auto-export Ableton presets")
    parser.add_argument("--test", action="store_true", help="Test with 1 file")
    parser.add_argument("--start", type=int, default=0, help="Start from N")
    parser.add_argument("--delay", type=float, default=5.0, help="Initial delay")
    args = parser.parse_args()

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    pending = get_pending_presets()
    if not pending:
        print("All presets already exported!")
        return 0

    if args.start > 0:
        pending = pending[args.start:]
    if args.test:
        pending = pending[:1]

    total = len(pending)
    print(f"Exporting {total} presets")
    print(f"Output: {AUDIO_DIR}")
    print(f"\nStarting in {args.delay}s - don't touch keyboard/mouse!")
    print("Move mouse to corner to abort\n")

    time.sleep(args.delay)
    activate_ableton()

    success = 0
    for idx, als_path in enumerate(pending, 1):
        try:
            if export_single_preset(als_path, idx, total):
                success += 1
        except pyautogui.FailSafeException:
            print("\nAborted (mouse in corner)")
            break
        except KeyboardInterrupt:
            print("\nAborted by user")
            break
        except Exception as e:
            print(f"    Error: {e}")

    print(f"\nDone! Exported {success}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
