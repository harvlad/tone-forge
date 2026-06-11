#!/bin/bash
# Start Tone Forge server with ONNX logging suppressed
#
# The ONNX runtime prints thousands of debug lines during MIDI extraction
# which severely slows down processing. These must be set BEFORE Python starts.

export ORT_LOGGING_LEVEL=3
export ONNX_LOG_LEVEL=3
export TF_CPP_MIN_LOG_LEVEL=2  # Suppress TensorFlow warnings too

# Disable ONNX optimizations that cause verbose output
export ORT_DISABLE_ALL_OPTIMIZATIONS=0

cd "$(dirname "$0")"
uvicorn tone_forge_api:app --reload --port 8000 "$@"
