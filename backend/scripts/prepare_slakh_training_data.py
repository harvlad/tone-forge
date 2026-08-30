#!/usr/bin/env python3
"""Prepare Slakh2100 stems for basic_pitch fine-tuning.

Converts audio+MIDI to TFRecord format compatible with basic_pitch training.
Supports all melodic instrument classes.
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional, Set
import yaml
import mido
import numpy as np
import librosa
import soundfile as sf
import tensorflow as tf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Instrument classes to exclude (non-melodic)
EXCLUDED_CLASSES = {
    'Drums',
    'Sound effects',
    'Sound Effects',
    'Percussive',
}

# All melodic instrument classes
MELODIC_CLASSES = {
    'Guitar', 'Piano', 'Bass', 'Brass', 'Synth Pad', 'Reed', 'Pipe',
    'Organ', 'Synth Lead', 'Strings', 'Strings (continued)',
    'Chromatic Percussion', 'Ethnic',
}

# basic_pitch constants
AUDIO_SAMPLE_RATE = 22050
FFT_HOP = 256
ANNOTATIONS_FPS = AUDIO_SAMPLE_RATE // FFT_HOP  # ~86 fps
ANNOTATION_HOP = 1.0 / ANNOTATIONS_FPS
N_FREQ_BINS_NOTES = 88  # Piano keys
N_FREQ_BINS_CONTOURS = 264  # 88 * 3 bins per semitone
CONTOURS_BINS_PER_SEMITONE = 3
ANNOTATIONS_BASE_FREQUENCY = 27.5  # A0 = MIDI 21


def midi_to_freq(midi_note: int) -> float:
    """Convert MIDI note number to frequency in Hz."""
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


def freq_to_note_bin(freq: float) -> int:
    """Convert frequency to note bin (0-87, representing MIDI 21-108)."""
    if freq <= 0:
        return -1
    midi_note = 69 + 12 * np.log2(freq / 440.0)
    note_bin = int(round(midi_note)) - 21  # A0 = MIDI 21 = bin 0
    if 0 <= note_bin < N_FREQ_BINS_NOTES:
        return note_bin
    return -1


def freq_to_contour_bin(freq: float) -> int:
    """Convert frequency to contour bin (0-263, 3 bins per semitone)."""
    if freq <= 0:
        return -1
    # Calculate semitone distance from A0 (27.5 Hz)
    semitones = 12 * np.log2(freq / ANNOTATIONS_BASE_FREQUENCY)
    # Each semitone has 3 bins, center bin at integer semitone
    contour_bin = int(round(semitones * CONTOURS_BINS_PER_SEMITONE))
    if 0 <= contour_bin < N_FREQ_BINS_CONTOURS:
        return contour_bin
    return -1


def load_midi_notes(midi_path: Path) -> List[Dict]:
    """Load notes from MIDI file as list of {pitch, onset, offset} dicts."""
    mid = mido.MidiFile(str(midi_path))

    tempo = 500000  # Default (120 BPM)
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    notes = []
    for track in mid.tracks:
        current_time = 0
        active_notes = {}

        for msg in track:
            current_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = (current_time, msg.velocity)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active_notes:
                    start, velocity = active_notes.pop(msg.note)
                    notes.append({
                        'pitch': msg.note,
                        'onset': start,
                        'offset': current_time,
                        'velocity': velocity,
                    })

    return notes


def notes_to_sparse_matrices(
    notes: List[Dict],
    duration: float
) -> Tuple[List, List, List, List, List, List, int]:
    """Convert note list to sparse matrices for notes, onsets, contours.

    Returns:
        note_indices, note_values,
        onset_indices, onset_values,
        contour_indices, contour_values,
        n_time_frames
    """
    time_scale = np.arange(0, duration + ANNOTATION_HOP, ANNOTATION_HOP)
    n_time_frames = len(time_scale)

    note_indices = []
    note_values = []
    onset_indices = []
    onset_values = []
    contour_indices = []
    contour_values = []

    for note in notes:
        pitch = note['pitch']
        onset = note['onset']
        offset = note['offset']
        velocity = note.get('velocity', 100) / 127.0  # Normalize

        freq = midi_to_freq(pitch)
        note_bin = freq_to_note_bin(freq)
        contour_bin = freq_to_contour_bin(freq)

        if note_bin < 0:
            continue

        # Find frame indices
        onset_frame = int(onset * ANNOTATIONS_FPS)
        offset_frame = int(offset * ANNOTATIONS_FPS)

        # Clamp to valid range
        onset_frame = max(0, min(onset_frame, n_time_frames - 1))
        offset_frame = max(0, min(offset_frame, n_time_frames - 1))

        # Add onset (single frame)
        onset_indices.append([onset_frame, note_bin])
        onset_values.append(1.0)

        # Add note frames (sustained)
        for frame in range(onset_frame, offset_frame + 1):
            if frame < n_time_frames:
                note_indices.append([frame, note_bin])
                note_values.append(1.0)

                # Add contour (pitch track)
                if 0 <= contour_bin < N_FREQ_BINS_CONTOURS:
                    contour_indices.append([frame, contour_bin])
                    contour_values.append(1.0)

    return (
        note_indices, note_values,
        onset_indices, onset_values,
        contour_indices, contour_values,
        n_time_frames
    )


def bytes_feature(value):
    """Create bytes feature for TFRecord."""
    if isinstance(value, type(tf.constant(0))):
        value = value.numpy()
    if not isinstance(value, list):
        value = [value]
    return tf.train.Feature(bytes_list=tf.train.BytesList(value=value))


def create_tfexample(
    file_id: str,
    source: str,
    audio_path: Path,
    notes: List[Dict],
    duration: float
) -> tf.train.Example:
    """Create a TFExample from audio and MIDI notes."""
    # Convert notes to sparse matrices
    (note_indices, note_values,
     onset_indices, onset_values,
     contour_indices, contour_values,
     n_time_frames) = notes_to_sparse_matrices(notes, duration)

    # Read audio as WAV bytes (must be 22050 Hz mono)
    with open(audio_path, 'rb') as f:
        encoded_wav = f.read()

    return tf.train.Example(
        features=tf.train.Features(
            feature={
                "file_id": bytes_feature(bytes(file_id, "utf-8")),
                "source": bytes_feature(bytes(source, "utf-8")),
                "audio_wav": bytes_feature(encoded_wav),
                "notes_indices": bytes_feature(
                    tf.io.serialize_tensor(np.array(note_indices, np.int64))
                ),
                "notes_values": bytes_feature(
                    tf.io.serialize_tensor(np.array(note_values, np.float32))
                ),
                "onsets_indices": bytes_feature(
                    tf.io.serialize_tensor(np.array(onset_indices, np.int64))
                ),
                "onsets_values": bytes_feature(
                    tf.io.serialize_tensor(np.array(onset_values, np.float32))
                ),
                "contours_indices": bytes_feature(
                    tf.io.serialize_tensor(np.array(contour_indices, np.int64))
                ),
                "contours_values": bytes_feature(
                    tf.io.serialize_tensor(np.array(contour_values, np.float32))
                ),
                "notes_onsets_shape": bytes_feature(
                    tf.io.serialize_tensor(np.array([n_time_frames, N_FREQ_BINS_NOTES], np.int64))
                ),
                "contours_shape": bytes_feature(
                    tf.io.serialize_tensor(np.array([n_time_frames, N_FREQ_BINS_CONTOURS], np.int64))
                ),
            }
        )
    )


def find_melodic_stems(
    dataset_dir: Path,
    inst_classes: Optional[Set[str]] = None
) -> List[Dict]:
    """Find melodic stems in Slakh dataset.

    Args:
        dataset_dir: Path to dataset (or split subdirectory)
        inst_classes: Set of classes to include. None = all melodic.
    """
    if inst_classes is None:
        inst_classes = MELODIC_CLASSES

    stems = []

    # Check if this is a split directory or root
    if dataset_dir.name in ["train", "test", "validation"]:
        search_dirs = [dataset_dir]
    else:
        search_dirs = []
        for subdir in ["train", "test", "validation"]:
            split_dir = dataset_dir / subdir
            if split_dir.exists():
                search_dirs.append(split_dir)
        if not search_dirs:
            search_dirs = [dataset_dir]

    for search_dir in search_dirs:
        for track_dir in sorted(search_dir.iterdir()):
            if not track_dir.is_dir() or not track_dir.name.startswith("Track"):
                continue

            metadata_path = track_dir / "metadata.yaml"
            if not metadata_path.exists():
                continue

            try:
                with open(metadata_path) as f:
                    metadata = yaml.safe_load(f)
                if not metadata or 'stems' not in metadata:
                    continue
            except Exception as e:
                continue  # Skip corrupt metadata silently

            for stem_id, stem_info in metadata.get('stems', {}).items():
                inst_class = stem_info.get('inst_class', '')
                if inst_class in inst_classes and inst_class not in EXCLUDED_CLASSES:
                    # Support both WAV (BabySlakh) and FLAC (Slakh2100)
                    audio_path_wav = track_dir / "stems" / f"{stem_id}.wav"
                    audio_path_flac = track_dir / "stems" / f"{stem_id}.flac"
                    midi_path = track_dir / "MIDI" / f"{stem_id}.mid"

                    audio_path = None
                    if audio_path_wav.exists():
                        audio_path = audio_path_wav
                    elif audio_path_flac.exists():
                        audio_path = audio_path_flac

                    if audio_path and midi_path.exists():
                        stems.append({
                            'track': track_dir.name,
                            'stem_id': stem_id,
                            'audio_path': audio_path,
                            'midi_path': midi_path,
                            'inst_class': inst_class,
                            'program_name': stem_info.get('midi_program_name', 'Unknown'),
                        })

    return stems


def convert_audio_to_22k_mono(input_path: Path, output_path: Path) -> float:
    """Convert audio to 22050 Hz mono WAV. Returns duration in seconds."""
    # Load and resample
    y, sr = librosa.load(str(input_path), sr=AUDIO_SAMPLE_RATE, mono=True)
    duration = len(y) / AUDIO_SAMPLE_RATE

    # Save as WAV
    sf.write(str(output_path), y, AUDIO_SAMPLE_RATE)

    return duration


def process_split(
    stems: List[Dict],
    output_path: Path,
    tmp_dir: Path,
    source_name: str = "slakh2100"
) -> int:
    """Process stems for a single split. Returns count of successful examples."""
    writer = tf.io.TFRecordWriter(str(output_path))
    success_count = 0

    for i, stem in enumerate(stems):
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(stems)}] processed...")

        # Convert audio
        tmp_audio = tmp_dir / f"{stem['track']}_{stem['stem_id']}.wav"
        try:
            duration = convert_audio_to_22k_mono(stem['audio_path'], tmp_audio)
        except Exception:
            continue

        # Load MIDI
        try:
            notes = load_midi_notes(stem['midi_path'])
        except Exception:
            continue
        if not notes:
            continue

        # Create TFExample
        try:
            example = create_tfexample(
                f"{stem['track']}_{stem['stem_id']}",
                source_name,
                tmp_audio,
                notes,
                duration
            )
            writer.write(example.SerializeToString())
            success_count += 1
        except Exception:
            continue

        # Clean up temp file
        if tmp_audio.exists():
            tmp_audio.unlink()

    writer.close()
    return success_count


def prepare_training_data(
    dataset_dir: Path,
    output_dir: Path,
    inst_classes: Optional[Set[str]] = None
):
    """Prepare TFRecord files for training using Slakh's native splits."""
    from collections import Counter

    # Create output directories
    output_dir.mkdir(parents=True, exist_ok=True)
    train_dir = output_dir / "melodic" / "splits" / "train"
    val_dir = output_dir / "melodic" / "splits" / "validation"
    train_dir.mkdir(parents=True, exist_ok=True)
    val_dir.mkdir(parents=True, exist_ok=True)

    # Temp directory for converted audio
    tmp_dir = output_dir / "tmp_audio"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Find stems per split (using Slakh's native splits)
    train_stems = find_melodic_stems(dataset_dir / "train", inst_classes)
    val_stems = find_melodic_stems(dataset_dir / "validation", inst_classes)

    print(f"Found {len(train_stems)} training stems")
    print(f"Found {len(val_stems)} validation stems")

    # Show class distribution
    train_classes = Counter(s['inst_class'] for s in train_stems)
    print("\nTraining class distribution:")
    for cls, count in train_classes.most_common():
        print(f"  {cls:25s}: {count}")

    # Process training stems
    print(f"\nProcessing {len(train_stems)} training stems...")
    train_count = process_split(
        train_stems,
        train_dir / "melodic.tfrecord",
        tmp_dir
    )
    print(f"  Wrote {train_count} training examples")

    # Process validation stems
    print(f"\nProcessing {len(val_stems)} validation stems...")
    val_count = process_split(
        val_stems,
        val_dir / "melodic.tfrecord",
        tmp_dir
    )
    print(f"  Wrote {val_count} validation examples")

    # Cleanup temp directory
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\nTFRecords written to {output_dir}")
    print(f"  Training: {train_dir} ({train_count} examples)")
    print(f"  Validation: {val_dir} ({val_count} examples)")


if __name__ == "__main__":
    dataset_dir = Path("/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux")
    output_dir = Path("/Users/mattharvey/Sites/tone-forge/datasets/basic_pitch_training")

    if not dataset_dir.exists():
        print(f"Dataset not found: {dataset_dir}")
        sys.exit(1)

    prepare_training_data(dataset_dir, output_dir)
