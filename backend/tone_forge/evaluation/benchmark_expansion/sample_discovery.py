"""Auto-discovery of benchmark samples from the samples directory.

Scans the samples/ directory structure and builds a DatasetManifest
with all available audio-MIDI pairs.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from .dataset_manifest import (
    BenchmarkSample,
    DatasetManifest,
    GenreSpec,
    SampleDifficulty,
    GENRE_SPECS,
)

logger = logging.getLogger(__name__)

# Stem type detection patterns
STEM_PATTERNS = {
    "bass": ["bass", "_bass"],
    "lead": ["lead", "_lead"],
    "pad": ["pad", "pads", "_pad"],
    "guitar": ["guitar", "_guitar"],
    "synth": ["synth", "_synth"],
    "piano": ["piano", "_piano"],
    "arp": ["arp", "_arp"],
}


def detect_stem_type(filename: str) -> str:
    """Detect stem type from filename."""
    lower = filename.lower()
    for stem_type, patterns in STEM_PATTERNS.items():
        if any(p in lower for p in patterns):
            return stem_type
    return "other"


def extract_song_name(directory_name: str) -> str:
    """Extract clean song name from directory like '08 - Demolition Warning'."""
    match = re.match(r'\d+\s*-\s*(.+)', directory_name)
    if match:
        return match.group(1).strip()
    return directory_name


def discover_samples(
    samples_dir: Path,
    genre: str = "synthwave",
) -> List[BenchmarkSample]:
    """Discover benchmark samples from a samples directory.

    Expected structure:
        samples_dir/
            01 - Song Name/
                SongName_120bpm.mid
                SongName_Bass.wav
                SongName_Lead.wav
                ...

    Args:
        samples_dir: Path to samples directory
        genre: Genre to assign to all samples

    Returns:
        List of discovered BenchmarkSample objects
    """
    samples = []
    samples_dir = Path(samples_dir)

    if not samples_dir.exists():
        logger.warning(f"Samples directory not found: {samples_dir}")
        return samples

    for song_dir in sorted(samples_dir.iterdir()):
        if not song_dir.is_dir():
            continue
        if song_dir.name.startswith('.'):
            continue

        song_name = extract_song_name(song_dir.name)
        logger.debug(f"Processing song: {song_name}")

        # Find the MIDI file (ground truth)
        midi_files = list(song_dir.glob("*.mid")) + list(song_dir.glob("*.midi"))
        midi_files = [m for m in midi_files if not m.name.startswith('_')]

        if not midi_files:
            logger.warning(f"No MIDI file found in {song_dir}")
            continue

        midi_file = midi_files[0]

        # Extract tempo from MIDI filename if available
        tempo_match = re.search(r'(\d+)bpm', midi_file.name, re.IGNORECASE)
        tempo = float(tempo_match.group(1)) if tempo_match else None

        # Find audio stems
        audio_files = list(song_dir.glob("*.wav"))
        audio_files = [a for a in audio_files if not a.name.startswith('_')]

        for audio_file in audio_files:
            stem_type = detect_stem_type(audio_file.stem)

            # Skip click tracks and drums (no melodic content)
            if 'click' in audio_file.name.lower():
                continue
            if stem_type == 'other' and 'drum' in audio_file.name.lower():
                continue

            # Create sample ID
            sample_id = f"{song_name.replace(' ', '')}_{stem_type}"

            sample = BenchmarkSample(
                id=sample_id,
                audio_path=str(audio_file),
                ground_truth_midi_path=str(midi_file),
                stem_type=stem_type,
                genre=genre,
                tempo_bpm=tempo,
                difficulty=SampleDifficulty.MEDIUM,
                source=f"toneforge_samples/{song_dir.name}",
            )
            samples.append(sample)
            logger.debug(f"  Added sample: {sample_id} ({stem_type})")

    logger.info(f"Discovered {len(samples)} samples from {samples_dir}")
    return samples


def build_manifest_from_samples(
    samples_dir: Path,
    name: str = "toneforge_samples",
    version: str = "1.0.0",
    genre: str = "synthwave",
) -> DatasetManifest:
    """Build a complete manifest from a samples directory.

    Args:
        samples_dir: Path to samples directory
        name: Manifest name
        version: Manifest version
        genre: Genre to assign (default synthwave for game music)

    Returns:
        DatasetManifest with all discovered samples
    """
    samples = discover_samples(samples_dir, genre=genre)

    # Get genre spec
    genre_spec = GENRE_SPECS.get(genre, GenreSpec(name=genre))

    manifest = DatasetManifest(
        name=name,
        version=version,
        description=f"Auto-discovered samples from {samples_dir.name}",
        genres=[genre_spec],
        samples=samples,
    )

    return manifest


def get_default_samples_dir() -> Path:
    """Get the default samples directory path."""
    # Try relative to backend
    backend_dir = Path(__file__).parent.parent.parent.parent
    samples_dir = backend_dir.parent / "samples"

    if samples_dir.exists():
        return samples_dir

    # Try absolute path
    return Path("/Users/mattharvey/Sites/tone-forge/samples")


def load_or_create_manifest(
    manifest_path: Optional[Path] = None,
    samples_dir: Optional[Path] = None,
) -> DatasetManifest:
    """Load existing manifest or create from samples directory.

    Args:
        manifest_path: Path to existing manifest JSON
        samples_dir: Path to samples directory for auto-discovery

    Returns:
        DatasetManifest ready for benchmarking
    """
    if manifest_path and manifest_path.exists():
        return DatasetManifest.load(manifest_path)

    if samples_dir is None:
        samples_dir = get_default_samples_dir()

    return build_manifest_from_samples(samples_dir)
