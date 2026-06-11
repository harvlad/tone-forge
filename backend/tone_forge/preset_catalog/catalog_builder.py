"""Preset catalog builder.

Orchestrates the full preset catalog pipeline:
1. Discover presets
2. Generate ALS files for rendering
3. Extract fingerprints from rendered audio
4. Build catalog manifest with similarity index
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib

import numpy as np

from .preset_discovery import (
    PresetInfo,
    detect_safe_filename_collisions,
    discover_presets,
    safe_filename,
)
from .preset_als_generator import generate_render_jobs, create_als_for_job, RenderJob

logger = logging.getLogger(__name__)


def _sha1_file(path: Path) -> str:
    """SHA-1 of file bytes (chunked, memory-bounded)."""
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha1_decoded_audio(path: Path) -> str:
    """SHA-1 of the librosa-decoded waveform at 48 kHz mono.

    This is the integrity-gate's "did the synth actually produce different
    sound?" check — independent of container metadata or channel layout.
    """
    import librosa

    y, _ = librosa.load(str(path), sr=48000, mono=True)
    # Quantise to int16 so floating-point rounding doesn't perturb the hash
    # across runs; the catalog-collapse signal we care about is much coarser.
    y_i16 = (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)
    return hashlib.sha1(y_i16.tobytes()).hexdigest()


@dataclass
class PresetFingerprint:
    """Fingerprint for a rendered preset.

    Uses the minimal 8-feature schema for Gate 1 validation.
    """
    # Identity
    preset_id: str
    preset_name: str
    instrument: str
    category: str
    sound_type: str

    # Core 8 features (sufficient for v1)
    brightness: float = 0.0       # Spectral centroid, normalized 0-1
    warmth: float = 0.0           # Low-mid energy ratio (200-800Hz / total)
    air: float = 0.0              # High frequency presence (8kHz+ / total)
    attack_ms: float = 0.0        # Time to peak amplitude
    decay_ms: float = 0.0         # Time from peak to sustain
    sustain_ratio: float = 0.0    # Sustain level / peak level
    harmonic_ratio: float = 0.0   # Harmonic vs noise content
    pitch_stability: float = 0.0  # How stable is the fundamental (1 = stable)

    # Optional extended features (for v2)
    stereo_width: float = 0.0
    modulation_depth: float = 0.0

    # Metadata
    audio_path: Optional[str] = None
    duration_sec: float = 0.0

    # Provenance chain (Catalog Integrity Gate inputs).
    # Tracks the full preset -> ALS -> WAV -> decoded-audio path with
    # content hashes so duplication can be detected at every stage.
    preset_path: Optional[str] = None        # Absolute path to source .adv
    adv_sha1: Optional[str] = None           # SHA-1 of .adv bytes
    als_path: Optional[str] = None           # Path to generated .als
    als_sha1: Optional[str] = None           # SHA-1 of .als bytes
    wav_sha1: Optional[str] = None           # SHA-1 of rendered WAV bytes
    decoded_audio_sha1: Optional[str] = None # SHA-1 of decoded 48 kHz mono samples
    test_sequence_name: Optional[str] = None # Name of the MIDI test sequence used

    def to_vector(self) -> np.ndarray:
        """Convert to 8-dim feature vector for similarity search."""
        return np.array([
            self.brightness,
            self.warmth,
            self.air,
            self.attack_ms / 500.0,  # Normalize to ~0-1
            self.decay_ms / 1000.0,
            self.sustain_ratio,
            self.harmonic_ratio,
            self.pitch_stability,
        ], dtype=np.float32)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "preset_id": self.preset_id,
            "preset_name": self.preset_name,
            "instrument": self.instrument,
            "category": self.category,
            "sound_type": self.sound_type,
            "features": {
                "brightness": self.brightness,
                "warmth": self.warmth,
                "air": self.air,
                "attack_ms": self.attack_ms,
                "decay_ms": self.decay_ms,
                "sustain_ratio": self.sustain_ratio,
                "harmonic_ratio": self.harmonic_ratio,
                "pitch_stability": self.pitch_stability,
            },
            "extended": {
                "stereo_width": self.stereo_width,
                "modulation_depth": self.modulation_depth,
            },
            "audio_path": self.audio_path,
            "duration_sec": self.duration_sec,
            "provenance": {
                "preset_path": self.preset_path,
                "adv_sha1": self.adv_sha1,
                "als_path": self.als_path,
                "als_sha1": self.als_sha1,
                "wav_sha1": self.wav_sha1,
                "decoded_audio_sha1": self.decoded_audio_sha1,
                "test_sequence_name": self.test_sequence_name,
            },
        }


def extract_preset_fingerprint(
    audio_path: Path,
    preset_info: PresetInfo,
    als_path: Optional[Path] = None,
    test_sequence_name: Optional[str] = None,
) -> PresetFingerprint:
    """Extract 8-feature fingerprint from rendered preset audio.

    Args:
        audio_path: Path to rendered WAV file
        preset_info: Preset metadata
        als_path: Optional path to the source ALS (for provenance manifest)
        test_sequence_name: Optional name of the MIDI test sequence used

    Returns:
        PresetFingerprint with extracted features and provenance fields
        populated.
    """
    import librosa
    from scipy import signal

    # Load audio
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)

    # Provenance: hash the rendered WAV bytes and the decoded waveform.
    wav_sha1 = _sha1_file(audio_path)
    decoded_sha1 = _sha1_decoded_audio(audio_path)

    # Source preset file (.adv) hashing — only if we can actually resolve it.
    preset_path: Optional[str] = None
    adv_sha1: Optional[str] = None
    if getattr(preset_info, "path", None):
        try:
            preset_path = str(preset_info.path)
            if Path(preset_path).exists():
                adv_sha1 = _sha1_file(Path(preset_path))
        except Exception as exc:
            logger.warning(
                "Failed to hash .adv for %s: %s", preset_info.preset_id, exc
            )

    als_path_str: Optional[str] = None
    als_sha1: Optional[str] = None
    if als_path is not None and Path(als_path).exists():
        als_path_str = str(als_path)
        try:
            als_sha1 = _sha1_file(Path(als_path))
        except Exception as exc:
            logger.warning(
                "Failed to hash ALS for %s: %s", preset_info.preset_id, exc
            )

    fingerprint = PresetFingerprint(
        preset_id=preset_info.preset_id,
        preset_name=preset_info.name,
        instrument=preset_info.instrument,
        category=preset_info.category,
        sound_type=preset_info.sound_type,
        audio_path=str(audio_path),
        duration_sec=len(y) / sr,
        preset_path=preset_path,
        adv_sha1=adv_sha1,
        als_path=als_path_str,
        als_sha1=als_sha1,
        wav_sha1=wav_sha1,
        decoded_audio_sha1=decoded_sha1,
        test_sequence_name=test_sequence_name,
    )

    # 1. Brightness (spectral centroid, normalized)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    fingerprint.brightness = float(np.mean(centroid) / (sr / 2))

    # 2. Warmth (200-800Hz energy ratio)
    D = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    low_mid_mask = (freqs >= 200) & (freqs <= 800)
    total_energy = np.sum(D ** 2)
    low_mid_energy = np.sum(D[low_mid_mask, :] ** 2)
    fingerprint.warmth = float(low_mid_energy / (total_energy + 1e-10))

    # 3. Air (8kHz+ energy ratio)
    high_mask = freqs >= 8000
    high_energy = np.sum(D[high_mask, :] ** 2)
    fingerprint.air = float(high_energy / (total_energy + 1e-10))

    # 4-6. Envelope characteristics (attack, decay, sustain)
    envelope = np.abs(librosa.effects.preemphasis(y))
    envelope = np.convolve(envelope, np.ones(512) / 512, mode='same')

    peak_idx = np.argmax(envelope)
    peak_val = envelope[peak_idx]

    # Attack time
    if peak_idx > 0 and peak_val > 0:
        threshold = peak_val * 0.1
        attack_start = 0
        for i in range(peak_idx):
            if envelope[i] > threshold:
                attack_start = i
                break
        attack_samples = peak_idx - attack_start
        fingerprint.attack_ms = float(attack_samples / sr * 1000)

    # Decay time (time to drop to 37% of peak)
    if peak_idx < len(envelope) - 1:
        decay_portion = envelope[peak_idx:]
        target = peak_val * 0.37
        decay_idx = np.argmax(decay_portion < target)
        if decay_idx > 0:
            fingerprint.decay_ms = float(decay_idx / sr * 1000)

        # Sustain ratio
        if len(decay_portion) > 0 and peak_val > 0:
            sustain_portion = decay_portion[len(decay_portion) // 2:]
            if len(sustain_portion) > 0:
                fingerprint.sustain_ratio = float(np.mean(sustain_portion) / peak_val)

    # 7. Harmonic ratio (harmonic vs percussive)
    y_harmonic, y_percussive = librosa.effects.hpss(y)
    harmonic_energy = np.sum(y_harmonic ** 2)
    fingerprint.harmonic_ratio = float(harmonic_energy / (total_energy + 1e-10))

    # 8. Pitch stability
    try:
        f0, voiced, probs = librosa.pyin(
            y, fmin=50, fmax=2000, sr=sr, hop_length=512
        )
        f0 = np.nan_to_num(f0, nan=0)
        voiced_f0 = f0[f0 > 0]

        if len(voiced_f0) > 10:
            # Stability = 1 - normalized std deviation
            std_cents = np.std(1200 * np.log2(voiced_f0 / np.mean(voiced_f0) + 1e-10))
            fingerprint.pitch_stability = float(max(0, 1 - std_cents / 100))
        else:
            fingerprint.pitch_stability = 0.5
    except Exception:
        fingerprint.pitch_stability = 0.5

    return fingerprint


@dataclass
class PresetCatalog:
    """Catalog of rendered presets with fingerprints."""

    presets: List[PresetFingerprint] = field(default_factory=list)
    version: str = "1.0"

    def add(self, fingerprint: PresetFingerprint):
        """Add a fingerprint to the catalog."""
        self.presets.append(fingerprint)

    def find_similar(
        self,
        query: PresetFingerprint,
        k: int = 5,
        instrument_filter: Optional[str] = None,
    ) -> List[Tuple[PresetFingerprint, float]]:
        """Find k most similar presets to query.

        Args:
            query: Query fingerprint
            k: Number of results
            instrument_filter: Optional filter by instrument

        Returns:
            List of (preset, distance) tuples, sorted by distance
        """
        query_vec = query.to_vector()
        results = []

        for preset in self.presets:
            # Skip if instrument filter doesn't match
            if instrument_filter and preset.instrument != instrument_filter:
                continue

            # Skip self-match
            if preset.preset_id == query.preset_id:
                continue

            preset_vec = preset.to_vector()
            distance = float(np.linalg.norm(query_vec - preset_vec))
            results.append((preset, distance))

        # Sort by distance (ascending)
        results.sort(key=lambda x: x[1])
        return results[:k]

    def build_similarity_matrix(self) -> np.ndarray:
        """Build pairwise similarity matrix for clustering analysis."""
        n = len(self.presets)
        vectors = np.array([p.to_vector() for p in self.presets])

        # Compute pairwise distances
        matrix = np.zeros((n, n))
        for i in range(n):
            for j in range(i + 1, n):
                dist = np.linalg.norm(vectors[i] - vectors[j])
                matrix[i, j] = dist
                matrix[j, i] = dist

        return matrix

    def save(self, path: Path):
        """Save catalog to JSON file."""
        data = {
            "version": self.version,
            "preset_count": len(self.presets),
            "presets": [p.to_dict() for p in self.presets],
        }
        path.write_text(json.dumps(data, indent=2))
        logger.info(f"Saved catalog with {len(self.presets)} presets to {path}")

    @classmethod
    def load(cls, path: Path) -> "PresetCatalog":
        """Load catalog from JSON file."""
        data = json.loads(path.read_text())
        catalog = cls(version=data.get("version", "1.0"))

        for p in data.get("presets", []):
            prov = p.get("provenance", {}) or {}
            fp = PresetFingerprint(
                preset_id=p["preset_id"],
                preset_name=p["preset_name"],
                instrument=p["instrument"],
                category=p["category"],
                sound_type=p["sound_type"],
                brightness=p["features"]["brightness"],
                warmth=p["features"]["warmth"],
                air=p["features"]["air"],
                attack_ms=p["features"]["attack_ms"],
                decay_ms=p["features"]["decay_ms"],
                sustain_ratio=p["features"]["sustain_ratio"],
                harmonic_ratio=p["features"]["harmonic_ratio"],
                pitch_stability=p["features"]["pitch_stability"],
                stereo_width=p.get("extended", {}).get("stereo_width", 0),
                modulation_depth=p.get("extended", {}).get("modulation_depth", 0),
                audio_path=p.get("audio_path"),
                duration_sec=p.get("duration_sec", 0),
                preset_path=prov.get("preset_path"),
                adv_sha1=prov.get("adv_sha1"),
                als_path=prov.get("als_path"),
                als_sha1=prov.get("als_sha1"),
                wav_sha1=prov.get("wav_sha1"),
                decoded_audio_sha1=prov.get("decoded_audio_sha1"),
                test_sequence_name=prov.get("test_sequence_name"),
            )
            catalog.add(fp)

        logger.info(f"Loaded catalog with {len(catalog.presets)} presets from {path}")
        return catalog


class CatalogBuilder:
    """Builds preset catalog from Ableton presets."""

    def __init__(
        self,
        output_dir: Path,
        instruments: Optional[List[str]] = None,
        tempo: float = 120.0,
    ):
        """Initialize catalog builder.

        Args:
            output_dir: Directory for all output files
            instruments: List of instruments to include (default: ["Analog"])
            tempo: BPM for rendering
        """
        self.output_dir = Path(output_dir)
        self.instruments = instruments or ["Analog"]
        self.tempo = tempo

        # Create subdirectories
        self.als_dir = self.output_dir / "als"
        self.audio_dir = self.output_dir / "audio"
        self.catalog_dir = self.output_dir / "catalog"

        for d in [self.als_dir, self.audio_dir, self.catalog_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def discover_presets(self) -> List[PresetInfo]:
        """Discover presets from Ableton."""
        logger.info(f"Discovering presets for: {self.instruments}")
        presets = discover_presets(self.instruments, include_packs=True)
        logger.info(f"Found {len(presets)} presets")
        return presets

    def generate_als_files(self, presets: List[PresetInfo]) -> List[Tuple[PresetInfo, Path]]:
        """Generate ALS files for all presets.

        Returns:
            List of (preset, als_path) tuples
        """
        logger.info(f"Generating ALS files for {len(presets)} presets")

        # Fail-loud on filename collisions before writing anything. Two
        # presets that resolve to the same safe_filename would silently
        # overwrite each other's ALS/WAV/catalog row otherwise — the
        # whole catalog-integrity story depends on a 1:1 preset↔stem map.
        collisions = detect_safe_filename_collisions(
            p.preset_id for p in presets
        )
        if collisions:
            sample = "; ".join(
                f"{stem!r} <- {ids}" for stem, ids in list(collisions.items())[:5]
            )
            raise ValueError(
                f"safe_filename() collision detected across {len(collisions)} "
                f"stem(s); preset_ids must produce unique filenames. "
                f"Examples: {sample}"
            )

        jobs = generate_render_jobs(presets, self.audio_dir, self.tempo)

        results = []
        for job in jobs:
            als_path = create_als_for_job(job, self.als_dir)
            results.append((job.preset, als_path))
            logger.debug(f"Created: {als_path.name}")

        logger.info(f"Generated {len(results)} ALS files in {self.als_dir}")
        return results

    def build_catalog_from_audio(
        self,
        presets: List[PresetInfo],
    ) -> PresetCatalog:
        """Build catalog from rendered audio files.

        Assumes audio files have been rendered to self.audio_dir.

        Args:
            presets: List of presets that were rendered

        Returns:
            PresetCatalog with fingerprints
        """
        catalog = PresetCatalog()

        for preset in presets:
            safe_name = safe_filename(preset.preset_id)
            audio_path = self.audio_dir / f"{safe_name}.wav"
            als_path = self.als_dir / f"{safe_name}.als"

            if not audio_path.exists():
                logger.warning(f"Audio not found: {audio_path}")
                continue

            # Best-effort: pass the ALS path through so provenance can
            # capture both ends of the rendering chain.
            als_path_arg: Optional[Path] = als_path if als_path.exists() else None

            try:
                fingerprint = extract_preset_fingerprint(
                    audio_path,
                    preset,
                    als_path=als_path_arg,
                )
                catalog.add(fingerprint)
                logger.debug(f"Fingerprinted: {preset.name}")
            except Exception as e:
                logger.error(f"Failed to fingerprint {preset.name}: {e}")

        logger.info(f"Built catalog with {len(catalog.presets)} fingerprints")
        return catalog

    def save_catalog(self, catalog: PresetCatalog, name: str = "preset_catalog"):
        """Save catalog to JSON."""
        catalog_path = self.catalog_dir / f"{name}.json"
        catalog.save(catalog_path)

    def generate_batch_render_script(self, presets: List[PresetInfo]) -> Path:
        """Generate AppleScript for batch rendering.

        This script opens each ALS in Ableton and exports to audio.

        Args:
            presets: List of presets to render

        Returns:
            Path to generated script
        """
        script_lines = [
            '-- ToneForge Preset Rendering Script',
            '-- Run this in Script Editor with Ableton Live open',
            '',
            'tell application "Ableton Live 12 Standard"',
            '    activate',
            'end tell',
            '',
            'delay 2',
            '',
        ]

        for preset in presets:
            safe_name = safe_filename(preset.preset_id)
            als_path = self.als_dir / f"{safe_name}.als"
            wav_path = self.audio_dir / f"{safe_name}.wav"

            script_lines.extend([
                f'-- Render: {preset.name}',
                f'set alsFile to POSIX file "{als_path}"',
                f'set wavFile to "{wav_path}"',
                '',
                'tell application "Ableton Live 12 Standard"',
                '    open alsFile',
                '    delay 3',
                '    -- Note: Manual export required, or use Ableton scripting API',
                'end tell',
                '',
            ])

        script_lines.extend([
            '-- Done',
            'display dialog "Rendering complete!" buttons {"OK"} default button "OK"',
        ])

        script_path = self.output_dir / "batch_render.scpt"
        script_path.write_text("\n".join(script_lines))

        logger.info(f"Generated batch render script: {script_path}")
        return script_path

    def generate_manual_render_instructions(self, presets: List[PresetInfo]) -> Path:
        """Generate instructions for manual batch rendering.

        For when automation isn't available.
        """
        lines = [
            "# ToneForge Preset Rendering Instructions",
            "",
            "## Setup",
            f"1. Output directory: {self.audio_dir}",
            f"2. ALS files location: {self.als_dir}",
            f"3. Total presets to render: {len(presets)}",
            "",
            "## Rendering Steps",
            "",
            "For each ALS file:",
            "1. Open in Ableton Live",
            "2. Press Play to hear the preset",
            "3. Export Audio/Video (Cmd+Shift+R)",
            "   - File Type: WAV",
            "   - Sample Rate: 44100",
            "   - Bit Depth: 16",
            "   - Render Start: 0",
            "   - Render Length: 10 seconds",
            f"   - Save to: {self.audio_dir}/[preset_name].wav",
            "",
            "## Preset List",
            "",
        ]

        for i, preset in enumerate(presets, 1):
            safe_name = safe_filename(preset.preset_id)
            lines.append(f"{i}. {preset.name} ({preset.category})")
            lines.append(f"   ALS: {safe_name}.als")
            lines.append(f"   WAV: {safe_name}.wav")
            lines.append("")

        instructions_path = self.output_dir / "RENDER_INSTRUCTIONS.md"
        instructions_path.write_text("\n".join(lines))

        logger.info(f"Generated render instructions: {instructions_path}")
        return instructions_path
