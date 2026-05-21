#!/usr/bin/env python3
"""
Build script for ToneForge Local Engine installer.

Creates standalone executables for macOS, Windows, and Linux.

Usage:
    python local_engine/build.py

Output:
    dist/ToneForge Local Engine.app  (macOS)
    dist/ToneForge Local Engine.exe  (Windows)
    dist/toneforge-local             (Linux)

Requirements:
    pip install pyinstaller

Note: The resulting package will be large (~2-3GB) due to PyTorch.
Consider using --onedir for faster startup, or --onefile for single binary.
"""

import subprocess
import sys
import platform
from pathlib import Path

# Paths
ROOT = Path(__file__).parent.parent
LOCAL_ENGINE = ROOT / "local_engine"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

APP_NAME = "ToneForge Local Engine"
VERSION = "0.1.0"


def check_pyinstaller():
    """Ensure PyInstaller is installed."""
    try:
        import PyInstaller
        print(f"PyInstaller {PyInstaller.__version__} found")
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def get_hidden_imports():
    """Get list of hidden imports that PyInstaller might miss."""
    return [
        # Core dependencies
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "starlette",
        "pydantic",
        # System tray
        "pystray",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        # Audio processing
        "librosa",
        "soundfile",
        "audioread",
        "resampy",
        # ML
        "torch",
        "torchaudio",
        "demucs",
        "demucs.pretrained",
        "demucs.apply",
        "basic_pitch",
        # Numpy/Scipy
        "numpy",
        "scipy",
        "scipy.signal",
        "scipy.fft",
        # Our modules
        "tone_forge",
        "tone_forge.analyzer",
        "tone_forge.stem_separator",
        "tone_forge.midi_extractor",
        "tone_forge.auto_detect",
        "local_engine.server",
    ]


def get_data_files():
    """Get data files to include."""
    import site
    data = []

    # Include tone_forge package data
    tone_forge = ROOT / "tone_forge"
    if tone_forge.exists():
        data.append((str(tone_forge), "tone_forge"))

    # Include data directory
    data_dir = ROOT / "data"
    if data_dir.exists():
        data.append((str(data_dir), "data"))

    # Find and include demucs package data (required for model loading)
    for site_dir in site.getsitepackages():
        demucs_dir = Path(site_dir) / "demucs"
        if demucs_dir.exists():
            # Include the remote directory with model configs
            remote_dir = demucs_dir / "remote"
            if remote_dir.exists():
                data.append((str(remote_dir), "demucs/remote"))
            break

    # Find and include basic_pitch package data (for MIDI extraction)
    for site_dir in site.getsitepackages():
        bp_dir = Path(site_dir) / "basic_pitch"
        if bp_dir.exists():
            data.append((str(bp_dir), "basic_pitch"))
            break

    return data


def build_macos():
    """Build macOS .app bundle."""
    print("Building for macOS...")

    hidden_imports = [f"--hidden-import={imp}" for imp in get_hidden_imports()]
    data_files = [f"--add-data={src}:{dst}" for src, dst in get_data_files()]

    # Collect package data for ML libraries
    collect_data = [
        "--collect-data=demucs",
        "--collect-data=basic_pitch",
    ]

    # Use tray.py as entry point for menu bar app
    cmd = [
        sys.executable, "-m", "PyInstaller",
        f"--name={APP_NAME}",
        "--windowed",
        "--onedir",
        "--osx-bundle-identifier=com.toneforge.localengine",
        *hidden_imports,
        *data_files,
        *collect_data,
        f"--distpath={DIST}",
        f"--workpath={BUILD}",
        "--noconfirm",
        str(LOCAL_ENGINE / "tray.py"),
    ]

    # Add icon if it exists
    icon_path = LOCAL_ENGINE / "icon.icns"
    if icon_path.exists():
        cmd.insert(4, f"--icon={icon_path}")

    subprocess.run(cmd, check=True, cwd=ROOT)

    print(f"\n✓ Built: {DIST}/{APP_NAME}.app")
    print(f"  To create DMG: hdiutil create -volname '{APP_NAME}' -srcfolder '{DIST}/{APP_NAME}.app' -ov -format UDZO '{DIST}/{APP_NAME}.dmg'")


def build_windows():
    """Build Windows .exe."""
    print("Building for Windows...")

    hidden_imports = [f"--hidden-import={imp}" for imp in get_hidden_imports()]
    data_files = [f"--add-data={src};{dst}" for src, dst in get_data_files()]  # Windows uses ;

    # Collect package data for ML libraries
    collect_data = [
        "--collect-data=demucs",
        "--collect-data=basic_pitch",
    ]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        f"--name={APP_NAME}",
        "--windowed",
        "--onedir",
        *hidden_imports,
        *data_files,
        *collect_data,
        f"--distpath={DIST}",
        f"--workpath={BUILD}",
        "--noconfirm",
        str(LOCAL_ENGINE / "tray.py"),
    ]

    # Add icon if it exists
    icon_path = LOCAL_ENGINE / "icon.ico"
    if icon_path.exists():
        cmd.insert(4, f"--icon={icon_path}")

    subprocess.run(cmd, check=True, cwd=ROOT)

    print(f"\n✓ Built: {DIST}\\{APP_NAME}\\{APP_NAME}.exe")


def build_linux():
    """Build Linux executable."""
    print("Building for Linux...")

    hidden_imports = [f"--hidden-import={imp}" for imp in get_hidden_imports()]
    data_files = [f"--add-data={src}:{dst}" for src, dst in get_data_files()]

    # Collect package data for ML libraries
    collect_data = [
        "--collect-data=demucs",
        "--collect-data=basic_pitch",
    ]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=toneforge-local",
        "--onefile",
        *hidden_imports,
        *data_files,
        *collect_data,
        f"--distpath={DIST}",
        f"--workpath={BUILD}",
        "--noconfirm",
        str(LOCAL_ENGINE / "tray.py"),
    ]

    subprocess.run(cmd, check=True, cwd=ROOT)

    print(f"\n✓ Built: {DIST}/toneforge-local")
    print("  Note: Linux tray requires libappindicator3 or similar")


def main():
    check_pyinstaller()

    system = platform.system()

    if system == "Darwin":
        build_macos()
    elif system == "Windows":
        build_windows()
    elif system == "Linux":
        build_linux()
    else:
        print(f"Unsupported platform: {system}")
        sys.exit(1)

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           Build Complete                                      ║
╠══════════════════════════════════════════════════════════════╣
║  The local engine has been packaged.                          ║
║                                                               ║
║  Users can download and run it - no Python needed.           ║
║  The web app will auto-detect it on localhost:7777.          ║
╚══════════════════════════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    main()
