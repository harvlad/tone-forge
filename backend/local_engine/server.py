#!/usr/bin/env python3
"""
ToneForge Local Engine

A lightweight local server that handles GPU-accelerated audio processing.
Runs on localhost:7777 and accepts jobs from the ToneForge web app.

Features:
- Stem separation (Demucs) on local GPU
- MIDI extraction (basic_pitch)
- Progress streaming via SSE
- Auto-detection by browser

Usage:
    python -m local_engine.server
    # or
    python local_engine/server.py

The web app will automatically detect and use this when available.
"""

import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("toneforge.local")

app = FastAPI(
    title="ToneForge Local Engine",
    description="GPU-accelerated audio processing for ToneForge",
    version="0.1.0",
)

# Allow CORS from any origin (needed for browser to call localhost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Status & Detection
# -----------------------------------------------------------------------------

def _get_device_info() -> dict:
    """Get info about available compute devices."""
    import torch

    info = {
        "pytorch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "mps_available": hasattr(torch.backends, "mps") and torch.backends.mps.is_available(),
        "device": "cpu",
        "device_name": "CPU",
    }

    if info["cuda_available"]:
        info["device"] = "cuda"
        info["device_name"] = torch.cuda.get_device_name(0)
    elif info["mps_available"]:
        info["device"] = "mps"
        info["device_name"] = "Apple Silicon GPU"

    return info


@app.get("/")
async def root():
    """Health check and capability advertisement."""
    device_info = _get_device_info()

    return {
        "service": "toneforge-local-engine",
        "version": "0.1.0",
        "status": "ready",
        "capabilities": [
            "stem_separation",
            "midi_extraction",
            "analysis",
        ],
        "device": device_info,
    }


@app.get("/health")
async def health():
    """Simple health check for detection."""
    return {"status": "ok", "service": "toneforge-local"}


# -----------------------------------------------------------------------------
# Stem Separation
# -----------------------------------------------------------------------------

class StemSeparationRequest(BaseModel):
    """Request for stem separation."""
    # Audio can be sent as base64 or file upload
    audio_base64: Optional[str] = None
    stems: list[str] = ["drums", "bass", "other", "vocals"]
    model: str = "htdemucs"


@app.post("/api/separate-stems")
async def separate_stems_endpoint(file: UploadFile = File(...)):
    """
    Separate audio into stems using Demucs.

    Returns SSE stream with progress updates and final result.
    """
    async def generate():
        try:
            # Save uploaded file
            with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'upload', 'message': 'File received'})}\n\n"

            # Import here to avoid slow startup
            from tone_forge.stem_separator import separate_all_stems

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'loading', 'message': 'Loading Demucs model...'})}\n\n"

            # Run separation
            start_time = time.time()
            stems = separate_all_stems(tmp_path)
            elapsed = time.time() - start_time

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'complete', 'message': f'Separation complete ({elapsed:.1f}s)'})}\n\n"

            # Return stem paths
            result = {
                "type": "result",
                "stems": {name: str(path) for name, path in stems.items()},
                "elapsed_seconds": elapsed,
                "device": _get_device_info()["device"],
            }
            yield f"data: {json.dumps(result)}\n\n"

            # Cleanup
            Path(tmp_path).unlink(missing_ok=True)

        except Exception as e:
            logger.exception("Stem separation failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -----------------------------------------------------------------------------
# MIDI Extraction
# -----------------------------------------------------------------------------

@app.post("/api/extract-midi")
async def extract_midi_endpoint(file: UploadFile = File(...), profile: str = "default"):
    """
    Extract MIDI from audio using basic_pitch.

    Returns SSE stream with progress and MIDI data.
    """
    async def generate():
        try:
            # Save uploaded file
            with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'upload', 'message': 'File received'})}\n\n"

            from tone_forge.midi_extractor import extract_midi_from_file

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'extracting', 'message': 'Extracting MIDI...'})}\n\n"

            start_time = time.time()
            midi_result = extract_midi_from_file(tmp_path, profile=profile)
            elapsed = time.time() - start_time

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'complete', 'message': f'Extraction complete ({elapsed:.1f}s)'})}\n\n"

            result = {
                "type": "result",
                "midi": midi_result,
                "elapsed_seconds": elapsed,
            }
            yield f"data: {json.dumps(result)}\n\n"

            Path(tmp_path).unlink(missing_ok=True)

        except Exception as e:
            logger.exception("MIDI extraction failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -----------------------------------------------------------------------------
# Full Analysis (Deep)
# -----------------------------------------------------------------------------

@app.post("/api/analyze-deep")
async def analyze_deep_endpoint(file: UploadFile = File(...)):
    """
    Full deep analysis with stem separation + MIDI extraction.

    This is the GPU-heavy operation that benefits most from local processing.
    Returns SSE stream with progress and complete analysis result.
    """
    async def generate():
        try:
            # Save uploaded file
            with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name

            filename = file.filename or "audio"
            yield f"data: {json.dumps({'type': 'progress', 'stage': 'upload', 'message': f'Processing {filename}...'})}\n\n"

            # Import modules
            from tone_forge.stem_separator import separate_all_stems
            from tone_forge.midi_extractor import extract_midi_from_file
            from tone_forge import analyzer
            from tone_forge.auto_detect import detect_audio_type

            device_info = _get_device_info()
            device_name = device_info["device_name"]

            # Step 1: Stem separation
            yield f"data: {json.dumps({'type': 'progress', 'stage': 'stems', 'progress': 0.1, 'message': f'Separating stems on {device_name}...'})}\n\n"

            start_time = time.time()
            stems = separate_all_stems(tmp_path)
            stem_time = time.time() - start_time

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'stems', 'progress': 0.5, 'message': f'Stems separated ({stem_time:.1f}s)'})}\n\n"

            # Step 2: MIDI extraction for each relevant stem
            midi_stems = {}
            stem_profiles = {
                "drums": "drums",
                "bass": "bass",
                "other": "synth",
                "guitar": "default",
            }

            for stem_name, stem_path in stems.items():
                if stem_name in stem_profiles:
                    yield f"data: {json.dumps({'type': 'progress', 'stage': 'midi', 'progress': 0.6, 'message': f'Extracting {stem_name} MIDI...'})}\n\n"

                    try:
                        profile = stem_profiles[stem_name]
                        midi_result = extract_midi_from_file(str(stem_path), profile=profile)
                        midi_stems[stem_name] = midi_result
                    except Exception as e:
                        logger.warning(f"MIDI extraction failed for {stem_name}: {e}")

            # Step 3: Analysis
            yield f"data: {json.dumps({'type': 'progress', 'stage': 'analysis', 'progress': 0.8, 'message': 'Analyzing tone...'})}\n\n"

            # Detect type and analyze
            detection = detect_audio_type(tmp_path)

            # Run appropriate analyzers based on detection
            result = {
                "detected_type": detection.primary_type,
                "detection": {
                    "is_guitar": detection.is_guitar,
                    "is_synth": detection.is_synth,
                    "is_bass": detection.is_bass,
                    "is_drums": detection.is_drums,
                    "summary": detection.summary,
                },
                "midi_stems": midi_stems,
                "stems_available": list(stems.keys()),
                "processing": {
                    "device": device_info["device"],
                    "device_name": device_name,
                    "stem_separation_seconds": stem_time,
                    "total_seconds": time.time() - start_time,
                },
            }

            # Add guitar analysis if detected
            if detection.is_guitar:
                from tone_forge import helix_translator, pedal_translator
                guitar_path = stems.get("guitar") or stems.get("other")
                if guitar_path:
                    descriptor = analyzer.analyze(str(guitar_path))
                    result["guitar"] = {
                        "descriptor": descriptor,
                        "platforms": {
                            "helix": helix_translator.translate(descriptor),
                            "pedals": pedal_translator.translate(descriptor),
                        },
                    }

            # Add bass analysis if detected
            if detection.is_bass and "bass" in stems:
                from tone_forge import bass_analyzer, bass_translator
                bass_desc = bass_analyzer.analyze(str(stems["bass"]))
                result["bass"] = {
                    "descriptor": bass_desc,
                    "recommendations": bass_translator.translate(bass_desc),
                }

            # Add drum analysis if detected
            if detection.is_drums and "drums" in stems:
                from tone_forge import drum_analyzer, drum_translator
                drum_desc = drum_analyzer.analyze(str(stems["drums"]))
                result["drums"] = {
                    "descriptor": drum_desc,
                    "machine_match": drum_translator.translate(drum_desc),
                }

            # Add synth analysis if detected
            if detection.is_synth:
                from tone_forge import synth_analyzer
                synth_path = stems.get("other") or tmp_path
                synth_desc = synth_analyzer.analyze(str(synth_path))
                result["synth"] = {
                    "descriptor": synth_desc,
                }

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'complete', 'progress': 1.0, 'message': 'Analysis complete'})}\n\n"

            # Send final result
            yield f"data: {json.dumps({'type': 'result', 'data': result})}\n\n"

            # Cleanup
            Path(tmp_path).unlink(missing_ok=True)
            for stem_path in stems.values():
                Path(stem_path).unlink(missing_ok=True)

        except Exception as e:
            logger.exception("Deep analysis failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -----------------------------------------------------------------------------
# ML Runtime & Plugin APIs
# -----------------------------------------------------------------------------

@app.get("/api/ml/status")
async def ml_status():
    """Get ML runtime status and capabilities."""
    try:
        from local_engine.ml_runtime import get_ml_status
        return get_ml_status()
    except Exception as e:
        logger.exception("Failed to get ML status")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.get("/api/ml/models")
async def list_models():
    """List all available models and their status."""
    try:
        from local_engine.ml_runtime import get_downloader, MODELS

        downloader = get_downloader()
        models = {}

        for model_id, model_info in MODELS.items():
            models[model_id] = {
                "name": model_info.name,
                "size_bytes": model_info.size_bytes,
                "size_class": model_info.size_class.value,
                "available": downloader.is_model_available(model_id),
                "status": downloader.get_model_status(model_id).value,
            }

        return {
            "models": models,
            "storage": downloader.get_storage_usage(),
        }
    except Exception as e:
        logger.exception("Failed to list models")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


class ModelDownloadRequest(BaseModel):
    """Request to download a model."""
    model_id: str


@app.post("/api/ml/download")
async def download_model(request: ModelDownloadRequest):
    """Download a specific ML model."""
    async def generate():
        try:
            from local_engine.ml_runtime import get_downloader, MODELS

            downloader = get_downloader()
            model_id = request.model_id

            if model_id not in MODELS:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Unknown model: {model_id}'})}\n\n"
                return

            model_info = MODELS[model_id]
            yield f"data: {json.dumps({'type': 'progress', 'stage': 'starting', 'message': f'Downloading {model_info.name}...'})}\n\n"

            result = downloader.download_model(model_id)

            if result:
                yield f"data: {json.dumps({'type': 'result', 'success': True, 'model_id': model_id})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Download failed'})}\n\n"

        except Exception as e:
            logger.exception("Model download failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.delete("/api/ml/models/{model_id}")
async def delete_model(model_id: str):
    """Delete a downloaded model."""
    try:
        from local_engine.ml_runtime import get_downloader

        downloader = get_downloader()
        downloader.delete_model(model_id)

        return {"success": True, "model_id": model_id}
    except Exception as e:
        logger.exception("Failed to delete model")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.get("/api/ml/capabilities")
async def ml_capabilities():
    """Get available ML inference capabilities."""
    try:
        from local_engine.ml_runtime import get_engine

        engine = get_engine()
        return {
            "capabilities": engine.get_available_capabilities(),
        }
    except Exception as e:
        logger.exception("Failed to get capabilities")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


# -----------------------------------------------------------------------------
# Plugin Scanner API
# -----------------------------------------------------------------------------

@app.get("/api/plugins")
async def list_plugins(
    query: str = "",
    format: Optional[str] = None,
    category: Optional[str] = None,
    favorites_only: bool = False,
):
    """List installed plugins."""
    try:
        from local_engine.plugin_scanner import get_database

        db = get_database()
        plugins = db.search_plugins(
            query=query,
            format=format,
            category=category,
            favorites_only=favorites_only,
        )

        return {
            "plugins": plugins,
            "count": len(plugins),
            "stats": db.get_stats(),
        }
    except Exception as e:
        logger.exception("Failed to list plugins")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.post("/api/plugins/scan")
async def scan_plugins():
    """Scan for installed plugins."""
    async def generate():
        try:
            import platform
            from local_engine.plugin_scanner import get_database

            system = platform.system()
            yield f"data: {json.dumps({'type': 'progress', 'stage': 'starting', 'message': f'Scanning plugins on {system}...'})}\n\n"

            if system == "Darwin":
                from local_engine.plugin_scanner.scanner_macos import MacOSPluginScanner
                scanner = MacOSPluginScanner()
            elif system == "Windows":
                from local_engine.plugin_scanner.scanner_windows import WindowsPluginScanner
                scanner = WindowsPluginScanner()
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Unsupported platform: {system}'})}\n\n"
                return

            plugins = scanner.scan_all()

            yield f"data: {json.dumps({'type': 'progress', 'stage': 'storing', 'message': f'Found {len(plugins)} plugins'})}\n\n"

            db = get_database()
            added = 0
            for plugin in plugins:
                if db.add_plugin(plugin):
                    added += 1

            yield f"data: {json.dumps({'type': 'result', 'success': True, 'found': len(plugins), 'added': added})}\n\n"

        except Exception as e:
            logger.exception("Plugin scan failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


class PluginMappingRequest(BaseModel):
    """Request to set plugin mapping."""
    plugin_id: str
    block_family: str
    block_type: str
    confidence: float = 1.0


@app.post("/api/plugins/mapping")
async def set_plugin_mapping(request: PluginMappingRequest):
    """Set a custom plugin-to-block mapping."""
    try:
        from local_engine.plugin_scanner import get_database

        db = get_database()
        db.set_block_mapping(
            plugin_id=request.plugin_id,
            block_family=request.block_family,
            block_type=request.block_type,
            confidence=request.confidence,
            user_set=True,
        )

        return {"success": True, "plugin_id": request.plugin_id}
    except Exception as e:
        logger.exception("Failed to set mapping")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.post("/api/plugins/{plugin_id}/favorite")
async def toggle_favorite(plugin_id: str, favorite: bool = True):
    """Toggle plugin favorite status."""
    try:
        from local_engine.plugin_scanner import get_database

        db = get_database()
        db.set_favorite(plugin_id, favorite)

        return {"success": True, "plugin_id": plugin_id, "favorite": favorite}
    except Exception as e:
        logger.exception("Failed to toggle favorite")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


# -----------------------------------------------------------------------------
# Preferences API
# -----------------------------------------------------------------------------

@app.get("/api/preferences")
async def get_preferences():
    """Get learned user preferences."""
    try:
        from tone_forge.ml.preferences import get_learner, get_tracker

        learner = get_learner()
        tracker = get_tracker()

        preferences = learner.learn_preferences()
        stats = tracker.get_aggregated_stats()

        return {
            "preferences": {
                "amp": {
                    "preferred_families": preferences.amp.preferred_families,
                    "gain_preference": preferences.amp.gain_preference,
                },
                "cab": {
                    "preferred_configs": preferences.cab.preferred_configs,
                    "preferred_speakers": preferences.cab.preferred_speakers,
                },
                "effects": {
                    "frequently_used": preferences.effects.frequently_used,
                    "rarely_used": preferences.effects.rarely_used,
                },
                "genre": {
                    "primary_genres": preferences.genre.primary_genres,
                    "affinities": preferences.genre.affinities,
                },
            },
            "stats": stats,
        }
    except Exception as e:
        logger.exception("Failed to get preferences")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.get("/api/preferences/privacy")
async def get_privacy_settings():
    """Get privacy settings and data summary."""
    try:
        from tone_forge.ml.preferences import get_privacy_manager

        manager = get_privacy_manager()
        return {
            "tracking_enabled": manager.is_tracking_enabled(),
            "data_summary": manager.get_data_summary(),
        }
    except Exception as e:
        logger.exception("Failed to get privacy settings")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


class TrackingSettingRequest(BaseModel):
    """Request to change tracking setting."""
    enabled: bool


@app.post("/api/preferences/tracking")
async def set_tracking(request: TrackingSettingRequest):
    """Enable or disable behavior tracking."""
    try:
        from tone_forge.ml.preferences import get_privacy_manager

        manager = get_privacy_manager()
        manager.set_tracking_enabled(request.enabled)

        return {"success": True, "tracking_enabled": request.enabled}
    except Exception as e:
        logger.exception("Failed to set tracking")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.post("/api/preferences/export")
async def export_preferences():
    """Export all preference data."""
    try:
        from tone_forge.ml.preferences import get_privacy_manager

        manager = get_privacy_manager()
        export_path = manager.export_all_data()

        return {
            "success": True,
            "export_path": str(export_path),
        }
    except Exception as e:
        logger.exception("Failed to export data")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.delete("/api/preferences/data")
async def delete_all_data():
    """Delete all collected preference data."""
    try:
        from tone_forge.ml.preferences import get_privacy_manager

        manager = get_privacy_manager()
        manager.delete_all_data()

        return {"success": True, "message": "All data deleted"}
    except Exception as e:
        logger.exception("Failed to delete data")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    """Run the local engine server."""
    import argparse

    parser = argparse.ArgumentParser(description="ToneForge Local Engine")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=7777, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    device_info = _get_device_info()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           ToneForge Local Engine v0.1.0                      ║
╠══════════════════════════════════════════════════════════════╣
║  Device: {device_info['device_name']:<50} ║
║  Compute: {device_info['device'].upper():<49} ║
║  URL: http://{args.host}:{args.port:<43} ║
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "local_engine.server:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
