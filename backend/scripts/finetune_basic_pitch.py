#!/usr/bin/env python3
"""Fine-tune basic_pitch on BabySlakh Synth Lead data.

Loads pre-trained ICASSP 2022 model and fine-tunes on synth lead data.
"""
import argparse
import logging
import os
from pathlib import Path
from typing import Tuple

import numpy as np
import tensorflow as tf
import librosa

logging.basicConfig(level=logging.INFO)


def augment_audio(audio, sample_rate=22050):
    """Apply random data augmentation to audio.

    Augmentations:
    - Pitch shift: ±3 semitones
    - Time stretch: 0.9-1.1x
    - Gain variation: ±6 dB
    """
    # Random pitch shift (-3 to +3 semitones)
    pitch_shift = np.random.uniform(-3, 3)
    audio = librosa.effects.pitch_shift(audio, sr=sample_rate, n_steps=pitch_shift)

    # Random time stretch (0.9x to 1.1x)
    stretch_rate = np.random.uniform(0.9, 1.1)
    audio = librosa.effects.time_stretch(audio, rate=stretch_rate)

    # Random gain (-6 to +6 dB)
    gain_db = np.random.uniform(-6, 6)
    audio = audio * (10 ** (gain_db / 20))

    # Clip to [-1, 1]
    audio = np.clip(audio, -1, 1)

    return audio.astype(np.float32)


def load_dataset(
    data_path: Path,
    split: str,
    batch_size: int = 8,
    shuffle: bool = True
) -> tf.data.Dataset:
    """Load TFRecord dataset for training/validation."""
    from basic_pitch.constants import (
        ANNOT_N_FRAMES,
        N_FREQ_BINS_NOTES,
        N_FREQ_BINS_CONTOURS,
        AUDIO_N_SAMPLES,
    )

    tfrecord_pattern = str(data_path / "synth_lead" / "splits" / split / "*.tfrecord")
    files = tf.io.gfile.glob(tfrecord_pattern)

    if not files:
        raise ValueError(f"No TFRecord files found at {tfrecord_pattern}")

    logging.info(f"Found {len(files)} TFRecord files for {split}")

    def parse_example(serialized_example):
        schema = {
            "file_id": tf.io.FixedLenFeature((), tf.string),
            "source": tf.io.FixedLenFeature((), tf.string),
            "audio_wav": tf.io.FixedLenFeature((), tf.string),
            "notes_indices": tf.io.FixedLenFeature((), tf.string),
            "notes_values": tf.io.FixedLenFeature((), tf.string),
            "onsets_indices": tf.io.FixedLenFeature((), tf.string),
            "onsets_values": tf.io.FixedLenFeature((), tf.string),
            "contours_indices": tf.io.FixedLenFeature((), tf.string),
            "contours_values": tf.io.FixedLenFeature((), tf.string),
            "notes_onsets_shape": tf.io.FixedLenFeature((), tf.string),
            "contours_shape": tf.io.FixedLenFeature((), tf.string),
        }
        example = tf.io.parse_single_example(serialized_example, schema)
        return example

    def decode_sparse(indices_tensor, values_tensor, dense_shape):
        """Convert sparse representation to dense tensor."""
        indices = tf.io.parse_tensor(indices_tensor, out_type=tf.int64)
        values = tf.io.parse_tensor(values_tensor, out_type=tf.float32)

        # Handle empty sparse tensors
        if tf.size(indices) == 0:
            return tf.zeros(dense_shape, dtype=tf.float32)

        if tf.rank(indices) != 2 and tf.size(indices) == 0:
            indices = tf.zeros([0, 2], dtype=tf.int64)

        sparse = tf.SparseTensor(indices=indices, values=values, dense_shape=dense_shape)
        return tf.sparse.to_dense(sparse, validate_indices=False)

    def process_example(example):
        """Process a single example into training format."""
        # Decode audio
        audio_wav = example["audio_wav"]
        audio, sample_rate = tf.audio.decode_wav(audio_wav, desired_channels=1)

        # Get shapes
        notes_shape = tf.io.parse_tensor(example["notes_onsets_shape"], out_type=tf.int64)
        contours_shape = tf.io.parse_tensor(example["contours_shape"], out_type=tf.int64)

        # Decode sparse matrices
        notes = decode_sparse(example["notes_indices"], example["notes_values"], notes_shape)
        onsets = decode_sparse(example["onsets_indices"], example["onsets_values"], notes_shape)
        contours = decode_sparse(example["contours_indices"], example["contours_values"], contours_shape)

        return audio, notes, onsets, contours

    def extract_window(audio, notes, onsets, contours):
        """Extract a random 2-second window for training."""
        from basic_pitch.constants import (
            AUDIO_SAMPLE_RATE,
            AUDIO_N_SAMPLES,
            ANNOT_N_FRAMES,
        )

        audio_samples = tf.shape(audio)[0]
        # Use exact basic_pitch constants
        samples_per_window = AUDIO_N_SAMPLES  # 43844, not 44100
        frames_per_window = ANNOT_N_FRAMES    # 172

        # Random start time
        max_start = tf.maximum(0, audio_samples - samples_per_window)
        audio_start = tf.random.uniform((), 0, max_start + 1, dtype=tf.int32)

        # Corresponding annotation frame start (proportional to audio position)
        total_frames = tf.shape(notes)[0]
        frame_start = tf.cast(
            tf.cast(audio_start, tf.float32) / tf.cast(audio_samples, tf.float32) * tf.cast(total_frames, tf.float32),
            tf.int32
        )

        # Extract windows
        audio_window = audio[audio_start:audio_start + samples_per_window]
        notes_window = notes[frame_start:frame_start + frames_per_window]
        onsets_window = onsets[frame_start:frame_start + frames_per_window]
        contours_window = contours[frame_start:frame_start + frames_per_window]

        # Pad if necessary
        audio_window = tf.pad(audio_window, [[0, samples_per_window - tf.shape(audio_window)[0]], [0, 0]])
        notes_window = tf.pad(notes_window, [[0, frames_per_window - tf.shape(notes_window)[0]], [0, 0]])
        onsets_window = tf.pad(onsets_window, [[0, frames_per_window - tf.shape(onsets_window)[0]], [0, 0]])
        contours_window = tf.pad(contours_window, [[0, frames_per_window - tf.shape(contours_window)[0]], [0, 0]])

        return audio_window, notes_window, onsets_window, contours_window

    def to_training_format(audio, notes, onsets, contours):
        """Convert to format expected by basic_pitch model."""
        # Set shapes explicitly
        audio = tf.ensure_shape(audio, (AUDIO_N_SAMPLES, 1))
        notes = tf.ensure_shape(notes, (ANNOT_N_FRAMES, N_FREQ_BINS_NOTES))
        onsets = tf.ensure_shape(onsets, (ANNOT_N_FRAMES, N_FREQ_BINS_NOTES))
        contours = tf.ensure_shape(contours, (ANNOT_N_FRAMES, N_FREQ_BINS_CONTOURS))

        targets = {
            "onset": onsets,
            "note": notes,
            "contour": contours,
        }
        weights = {
            "onset": 1.0,
            "note": 1.0,
            "contour": 1.0,
        }
        return audio, targets, weights

    def apply_augmentation(audio, notes, onsets, contours):
        """Apply audio augmentation using tf.py_function."""
        def augment_fn(audio_tensor):
            audio_np = audio_tensor.numpy().flatten()
            augmented = augment_audio(audio_np, sample_rate=22050)
            # Ensure correct length after time stretch
            from basic_pitch.constants import AUDIO_N_SAMPLES
            if len(augmented) > AUDIO_N_SAMPLES:
                augmented = augmented[:AUDIO_N_SAMPLES]
            elif len(augmented) < AUDIO_N_SAMPLES:
                augmented = np.pad(augmented, (0, AUDIO_N_SAMPLES - len(augmented)))
            return augmented.reshape(-1, 1).astype(np.float32)

        augmented_audio = tf.py_function(
            augment_fn,
            [audio],
            tf.float32
        )
        from basic_pitch.constants import AUDIO_N_SAMPLES
        augmented_audio.set_shape((AUDIO_N_SAMPLES, 1))
        return augmented_audio, notes, onsets, contours

    # Build dataset pipeline
    ds = tf.data.TFRecordDataset(files)
    ds = ds.map(parse_example, num_parallel_calls=tf.data.AUTOTUNE)
    ds = ds.map(process_example, num_parallel_calls=tf.data.AUTOTUNE)
    ds = ds.map(extract_window, num_parallel_calls=tf.data.AUTOTUNE)

    # Apply augmentation only during training (when shuffle=True)
    if shuffle:
        ds = ds.map(apply_augmentation, num_parallel_calls=tf.data.AUTOTUNE)

    ds = ds.map(to_training_format, num_parallel_calls=tf.data.AUTOTUNE)

    if shuffle:
        ds = ds.shuffle(100)

    ds = ds.repeat()
    ds = ds.batch(batch_size)
    ds = ds.prefetch(tf.data.AUTOTUNE)

    return ds


def create_loss():
    """Create loss function matching basic_pitch training."""
    from basic_pitch.constants import ANNOT_N_FRAMES, N_FREQ_BINS_NOTES, N_FREQ_BINS_CONTOURS

    def loss_fn(y_true, y_pred):
        return tf.keras.losses.binary_crossentropy(y_true, y_pred, from_logits=False)

    return {
        "onset": loss_fn,
        "note": loss_fn,
        "contour": loss_fn,
    }


def main(
    data_path: str,
    output_path: str,
    pretrained_path: str = None,
    batch_size: int = 8,
    learning_rate: float = 0.0001,
    epochs: int = 50,
    steps_per_epoch: int = 50,
    validation_steps: int = 10,
):
    """Run fine-tuning."""
    data_path = Path(data_path)
    output_path = Path(output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    logging.info(f"Data path: {data_path}")
    logging.info(f"Output path: {output_path}")

    # Load datasets
    train_ds = load_dataset(data_path, "train", batch_size=batch_size, shuffle=True)
    val_ds = load_dataset(data_path, "validation", batch_size=batch_size, shuffle=False)

    # Create fresh trainable model
    from basic_pitch import models, ICASSP_2022_MODEL_PATH
    model = models.model()

    # Transfer pretrained weights from SavedModel
    logging.info("Loading pretrained weights from SavedModel...")
    pretrained = tf.saved_model.load(str(ICASSP_2022_MODEL_PATH))

    # Build mapping: pretrained var name -> numpy array
    pretrained_vars = {}
    for v in pretrained.variables:
        name = v.name.replace(':0', '')
        pretrained_vars[name] = v.numpy()

    # Transfer weights by matching names
    transferred = 0
    for layer in model.layers:
        if not layer.weights:
            continue
        for w in layer.weights:
            fresh_name = w.name.replace(':0', '')
            parts = fresh_name.split('/')
            # Map "layer/layer/weight" to "layer/weight"
            pretrained_name = f"{parts[0]}/{parts[-1]}"

            if pretrained_name in pretrained_vars:
                pretrained_val = pretrained_vars[pretrained_name]
                if w.shape == pretrained_val.shape:
                    w.assign(pretrained_val)
                    transferred += 1

    logging.info(f"Transferred {transferred}/{len(pretrained_vars)} pretrained weights")

    # Compile model
    model.compile(
        loss=create_loss(),
        optimizer=tf.keras.optimizers.Adam(learning_rate),
        metrics=["accuracy"],
    )

    model.summary()

    # Callbacks
    callbacks = [
        tf.keras.callbacks.EarlyStopping(patience=10, verbose=2, restore_best_weights=True),
        tf.keras.callbacks.ReduceLROnPlateau(verbose=1, patience=5, factor=0.5),
        tf.keras.callbacks.ModelCheckpoint(
            filepath=str(output_path / "model_best"),
            save_best_only=True,
            save_weights_only=False,
        ),
        tf.keras.callbacks.TensorBoard(log_dir=str(output_path / "logs")),
    ]

    # Train
    logging.info("Starting training...")
    model.fit(
        train_ds,
        epochs=epochs,
        steps_per_epoch=steps_per_epoch,
        validation_data=val_ds,
        validation_steps=validation_steps,
        callbacks=callbacks,
    )

    # Save final model
    model.save(str(output_path / "model_final"))
    logging.info(f"Model saved to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fine-tune basic_pitch on synth lead data")
    parser.add_argument("--data", required=True, help="Path to training data directory")
    parser.add_argument("--output", required=True, help="Path to save fine-tuned model")
    parser.add_argument("--pretrained", default=None, help="Path to pretrained model (optional)")
    parser.add_argument("--batch-size", type=int, default=8, help="Batch size")
    parser.add_argument("--learning-rate", type=float, default=0.0001, help="Learning rate")
    parser.add_argument("--epochs", type=int, default=50, help="Number of epochs")
    parser.add_argument("--steps-per-epoch", type=int, default=50, help="Steps per epoch")
    parser.add_argument("--validation-steps", type=int, default=10, help="Validation steps")

    args = parser.parse_args()

    main(
        args.data,
        args.output,
        args.pretrained,
        args.batch_size,
        args.learning_rate,
        args.epochs,
        args.steps_per_epoch,
        args.validation_steps,
    )
