"""FastAPI server for Tone Forge.

Endpoints + static file serving:
  GET  /              -> static/index.html
  GET  /static/*      -> static assets
  POST /api/analyze   -> accepts a WAV/MP3, returns descriptor + chain card
  POST /api/analyze-url -> accepts a YouTube URL, downloads audio, returns descriptor + chain

Run:
  cd backend
  uvicorn tone_forge_api:app --reload --port 8000

Then open http://localhost:8000 in a browser.
"""
from __future__ import annotations

import os

# Suppress ONNX Runtime verbose logging BEFORE any imports that might load it
# This prevents thousands of debug prints that slow down MIDI extraction significantly
os.environ["ORT_LOGGING_LEVEL"] = "3"  # ERROR only
os.environ["ONNX_LOG_LEVEL"] = "3"

import json
import logging
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

# Also set ONNX runtime severity after import
try:
    import onnxruntime as ort
    ort.set_default_logger_severity(3)  # ERROR only
except ImportError:
    pass


class NumpyJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _convert_numpy_types(obj):
    """Recursively convert numpy types to native Python types."""
    if isinstance(obj, dict):
        return {k: _convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_numpy_types(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(_convert_numpy_types(v) for v in obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        # Handle NaN and Inf which aren't valid JSON
        if np.isnan(obj):
            return None
        if np.isinf(obj):
            return None
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, float):
        # Handle Python float NaN/Inf too
        if obj != obj:  # NaN check
            return None
        if obj == float('inf') or obj == float('-inf'):
            return None
    return obj


def _compute_waveform_peaks(y: np.ndarray, num_points: int = 1000) -> dict:
    """Compute waveform peaks for visualization.

    Downsamples audio to a fixed number of points showing min/max envelope.

    Args:
        y: Audio samples (mono)
        num_points: Number of points for visualization

    Returns:
        Dict with 'peaks_positive', 'peaks_negative', 'rms' arrays
    """
    if len(y) == 0:
        return {"peaks_positive": [], "peaks_negative": [], "rms": []}

    # Ensure we don't have more points than samples
    num_points = min(num_points, len(y))

    # Compute chunk size
    chunk_size = max(1, len(y) // num_points)
    actual_points = len(y) // chunk_size

    peaks_pos = []
    peaks_neg = []
    rms_values = []

    for i in range(actual_points):
        start = i * chunk_size
        end = start + chunk_size
        chunk = y[start:end]

        peaks_pos.append(float(np.max(chunk)))
        peaks_neg.append(float(np.min(chunk)))
        rms_values.append(float(np.sqrt(np.mean(chunk ** 2))))

    return {
        "peaks_positive": peaks_pos,
        "peaks_negative": peaks_neg,
        "rms": rms_values,
        "sample_rate": None,  # Will be set by caller
        "duration_sec": None,  # Will be set by caller
    }


import asyncio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tone_forge import analyzer, helix_translator
from tone_forge import translator
from tone_forge.hardware import Platform

# Reconstruction pipeline for quality-aware analysis
_RECONSTRUCTION_AVAILABLE = False
try:
    from tone_forge.reconstruction.pipeline import get_pipeline, ReconstructionConfig
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    pass

# Analysis modes and spectral caching
from tone_forge.analysis_modes import (
    AnalysisMode,
    AnalysisConfig,
    get_config as get_analysis_config,
    describe_mode,
    estimate_time,
)
from tone_forge.spectral_cache import (
    SpectralFeatureCache,
    detect_genre_cached,
    detect_extraction_method_cached,
)

# Unified analysis pipeline
from tone_forge.unified_pipeline import (
    UnifiedPipeline,
    PipelineConfig,
    AnalysisMode as UnifiedAnalysisMode,
    ProgressEvent,
    AnalysisResult,
    get_pipeline as get_unified_pipeline,
)

# Session engine — canonical SessionBundle assembler (Priority 5).
from tone_forge.session import build as build_session_bundle, serialize as serialize_session_bundle
from tone_forge.session.protocol import ErrorCode, PeerLeftReason

# Device discovery — onboarding preferences + DeviceCaps mapping (Priority 7).
# Composition lives at the API edge so devices/ stays boundary-clean
# (devices/ only imports from contracts, not from session/).
from tone_forge.devices import (
    caps_from_preferences as _caps_from_preferences,
    clear_preferences as _clear_device_preferences,
    load_preferences as _load_device_preferences,
    probe as _device_probe,
    save_preferences as _save_device_preferences,
)
from tone_forge.contracts import (
    AudioDeviceInfo as _AudioDeviceInfo,
    DeviceCaps as _DeviceCaps,
    DeviceClass as _DeviceClass,
    DevicePreferences as _DevicePreferences,
    DeviceProbe as _DeviceProbe,
    MonitorChainFamily as _MonitorChainFamily,
)

# Tone retrieval — calibration + tier classifier + fallback policy (Priority 6).
# Composition lives at the API edge so session/ stays boundary-clean.
from tone_forge import tone as tone_retrieval
from tone_forge.contracts import UserRole as _UserRole

# Monitor chain bank — curated fallback chains (Priority 3). Resolved at
# the API edge so the Connect side ships without a YAML parser.
from tone_forge.contracts import MonitorChain as _MonitorChain
from tone_forge.monitor import (
    ChainNotFoundError as _ChainNotFoundError,
    ChainSpecError as _ChainSpecError,
    load_chain as _load_monitor_chain,
)


def _monitor_chain_to_wire(chain: _MonitorChain) -> dict:
    """Project a ``MonitorChain`` dataclass into the JSON-safe shape that
    crosses the connect-bridge socket.

    The ``MonitorChainFamily`` enum is collapsed to its string value so
    the Swift side doesn't need to mirror the enum's symbol space.
    ``parameters`` is forwarded verbatim — the loader already proved
    it's a plain dict.
    """

    return {
        "id": chain.id,
        "family": chain.family.value,
        "display_name": chain.display_name,
        "description": chain.description,
        "parameters": chain.parameters,
    }

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# History Storage
# -----------------------------------------------------------------------------
_HISTORY_FILE = Path(__file__).parent / "data" / "history.json"


def _load_history() -> list[dict]:
    """Load history from JSON file."""
    if not _HISTORY_FILE.exists():
        return []
    try:
        with open(_HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(history: list[dict]) -> None:
    """Save history to JSON file."""
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, cls=NumpyJSONEncoder)


def _add_to_history(entry: dict, full_result: dict = None) -> dict:
    """Add an entry to history and return it with ID.

    Args:
        entry: Metadata about the analysis (name, detected_type, etc.)
        full_result: The complete analysis result for reloading later
    """
    history = _load_history()
    entry["id"] = str(uuid.uuid4())[:8]
    entry["timestamp"] = datetime.now().isoformat()
    if full_result:
        entry["result"] = full_result
    history.insert(0, entry)  # Most recent first
    # Keep only last 100 entries
    history = history[:100]
    _save_history(history)
    return entry


def _get_history_item(entry_id: str) -> dict | None:
    """Get a specific history entry by ID."""
    history = _load_history()
    for entry in history:
        if entry.get("id") == entry_id:
            return entry
    return None

# Supported platforms
SUPPORTED_PLATFORMS = ["helix", "pedals", "synth"]


app = FastAPI(title="Tone Forge", version="0.5.0")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/analysis/{analysis_id}")
async def analysis_page(analysis_id: str) -> FileResponse:
    """Serve the app for a specific analysis (shareable URL)."""
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/jam")
async def jam_page() -> FileResponse:
    """ToneForge Jam — paste a song, separate stems, drop into a session."""
    return FileResponse(_STATIC_DIR / "jam.html")


@app.get("/jam/{analysis_id}")
async def jam_session_page(analysis_id: str) -> FileResponse:
    """Shareable URL for a Jam session pinned to an existing analysis."""
    return FileResponse(_STATIC_DIR / "jam.html")


# ---------------------------------------------------------------------------
# Connect bridge — WebSocket hub for browser ↔ Connect (native macOS app).
#
# The browser pushes tone-preset payloads here when an analysis lands; the
# server caches the latest payload and broadcasts it to every other client
# connected to the same channel. When the Swift Connect app later joins as
# a peer it receives the cached preset immediately and any subsequent
# pushes in real time.
#
# Protocol versioning (per ONBOARDING_AUDIT §F4.3):
#   * v0 — pre-versioning hello: {"type":"hello","role":...,"session_id":...}
#   * v1 — same shape + "protocol_version": 1. Server replies with
#          {"type":"hello_ack","protocol_version":1} immediately after
#          the hello and before "joined".
#   The server accepts v0 hellos for back-compat with the previous
#   Connect helper, and rejects requested versions strictly greater
#   than CONNECT_BRIDGE_PROTOCOL_VERSION with a "version_mismatch"
#   frame and an immediate close.
#
# Message envelope (JSON):
#   { "type": "preset_push", "session_id": "...", "preset": {...} }
#   { "type": "hello",       "role": "browser" | "connect", "session_id": "...",
#                            "protocol_version": 1 }
#   { "type": "hello_ack",   "protocol_version": 1 }
#   { "type": "version_mismatch", "required": N }
#   { "type": "ack",         "request_id": "..." }
# ---------------------------------------------------------------------------

# Bump whenever a required field is added or a message semantic changes.
# Both the Swift client (ConnectCore.ConnectProtocol.version) and the
# browser (jam.js CONNECT_PROTOCOL_VERSION) must stay in lockstep.
#
# v2 (Audio-Ownership Pivot): purely additive over v1. New message
# types — `session_data`, `transport_state`, `connect_state`,
# `latency_report`, `input_meter`. Existing v1 frames unchanged.
# A v1 client connecting to a v2 server still negotiates fine
# (client_version <= server_version is the accept path); the
# additional cached frames in join() are gated on "non-None" so a
# v1-only channel never sees a v2 replay.
CONNECT_BRIDGE_PROTOCOL_VERSION = 2

# Heartbeat / liveness detection for the WS bridge.
#
# The focused hardening pass plugged the broadcast-failure path: when a send
# raises, the dead client is reaped and surviving peers are notified. But a
# silently-dropped peer (laptop lid closed, network NAT timeout, browser tab
# killed without an OS-level teardown) does not trigger a send failure on
# its own — it just sits in `clients` until the next outbound frame happens
# to fail. Connect can go for hours without sending unsolicited frames, so
# the gap is real.
#
# The pattern below ties liveness to the existing receive loop instead of
# spawning a parallel task per socket. After RECV_TIMEOUT_SEC of silence we
# probe with a ping; if no frame at all comes back within PONG_TIMEOUT_SEC
# (a pong is enough, but any frame counts as proof of life), we treat the
# peer as gone, notify the survivors, and tear the socket down. A chatty
# client never hits the probe path — receive activity is itself the
# liveness signal.
CONNECT_BRIDGE_RECV_TIMEOUT_SEC = float(
    os.environ.get("TONEFORGE_CONNECT_RECV_TIMEOUT_SEC", "30")
)
CONNECT_BRIDGE_PONG_TIMEOUT_SEC = float(
    os.environ.get("TONEFORGE_CONNECT_PONG_TIMEOUT_SEC", "10")
)

# In-memory channel state. Survives only as long as the API process runs;
# clients re-push on reconnect.
class _ConnectChannel:
    def __init__(self, session_id: str | None = None) -> None:
        # Session id this channel was created for. Carried so downstream
        # helpers (telemetry, logs) can attribute events without
        # threading the id through every call site.
        self.session_id: str | None = session_id
        self.clients: set[WebSocket] = set()
        self.last_preset: dict | None = None
        # Last requested monitor gain (0.0 = muted, 1.0 = unity). We
        # cache it the same way as last_preset so a reconnecting Connect
        # helper restores the gain the user dialed in, instead of
        # snapping back to the muted default.
        self.last_gain: float | None = None
        # Last applied monitor chain (resolved spec, not just the id).
        # Same replay rationale: a reconnecting Connect helper rebuilds
        # the AVAudioEngine graph from this cached spec without needing
        # to query the server again. ``None`` means "no chain applied
        # this session" — Connect falls back to its dry passthrough.
        self.last_chain: dict | None = None

        # ----- v2 cache fields (Audio-Ownership Pivot) -----
        #
        # These mirror last_preset / last_gain / last_chain but for the
        # v2 frame types. The high-rate v2 types (transport_state and
        # input_meter) are intentionally *not* cached: they are
        # stale-on-arrival and broadcasting a stale tick on join would
        # be misleading. The low-rate snapshot types (connect_state,
        # latency_report) and session-scoped session_data ARE cached
        # so a late-joining peer (e.g. a JAM tab opened after Connect
        # is already running, or vice-versa) sees current truth.
        self.last_connect_state: dict | None = None
        self.last_latency_report: dict | None = None
        self.last_session_data: dict | None = None
        # Post-pivot stem playback: the v2 ``load_stems`` frame
        # hands a list of stem URLs from JAM to Connect. Cached
        # so a Connect helper that joins mid-song (relaunch, or
        # the user opened JAM before installing the helper) sees
        # the current stem set on join. The URLs already point at
        # the backend's own ``/api/serve-file`` endpoint so the
        # cache holds plain JSON; no audio bytes live here.
        self.last_load_stems: dict | None = None

    async def join(self, ws: WebSocket) -> None:
        self.clients.add(ws)
        if self.last_preset is not None:
            try:
                await ws.send_json({"type": "preset_push", "preset": self.last_preset, "replayed": True})
            except Exception:
                pass
        if self.last_gain is not None:
            try:
                await ws.send_json({"type": "set_gain", "gain": self.last_gain, "replayed": True})
            except Exception:
                pass
        if self.last_chain is not None:
            try:
                await ws.send_json({
                    "type": "apply_chain",
                    "chain_id": self.last_chain.get("id"),
                    "chain": self.last_chain,
                    "replayed": True,
                })
            except Exception:
                pass
        # ----- v2 replay (Audio-Ownership Pivot) -----
        #
        # Snapshot frames replayed on join so a late-joining peer sees
        # current truth without waiting for the next 1-Hz tick from
        # Connect or the next analysis-complete event from JAM. Each
        # carries replayed=True so the receiver can distinguish a
        # replay from a fresh push (useful for "first connect_state
        # received" UI logic).
        if self.last_connect_state is not None:
            try:
                payload = dict(self.last_connect_state)
                payload["replayed"] = True
                await ws.send_json(payload)
            except Exception:
                pass
        if self.last_latency_report is not None:
            try:
                payload = dict(self.last_latency_report)
                payload["replayed"] = True
                await ws.send_json(payload)
            except Exception:
                pass
        if self.last_session_data is not None:
            try:
                payload = dict(self.last_session_data)
                payload["replayed"] = True
                await ws.send_json(payload)
            except Exception:
                pass
        if self.last_load_stems is not None:
            try:
                payload = dict(self.last_load_stems)
                payload["replayed"] = True
                await ws.send_json(payload)
            except Exception:
                pass
        # Auto-update preference is *global* (lives in device.json), not
        # per-channel, so we read it from disk on every join rather than
        # caching on the channel. A fresh Connect helper that spawned
        # after the user toggled the preference in another tab must see
        # the current value or Sparkle will run with stale defaults.
        # ``None`` means "user has not expressed a preference" — let
        # Sparkle's built-in default apply, no replay needed.
        try:
            disk_prefs = _load_device_preferences()
        except Exception:
            disk_prefs = None
        if disk_prefs is not None and disk_prefs.auto_update_enabled is not None:
            try:
                await ws.send_json({
                    "v": 1,
                    "type": "set_auto_update",
                    "enabled": disk_prefs.auto_update_enabled,
                    "replayed": True,
                })
            except Exception:
                pass

    async def leave(self, ws: WebSocket) -> None:
        self.clients.discard(ws)
        # When the last peer leaves, drop the channel from the registry
        # so per-session state (last_preset/last_gain/last_chain) does
        # not accumulate. Every browser reload picks a fresh session id;
        # without this reap we'd leak ~1KB plus the cached chain spec
        # per reload.
        #
        # No lock here: the asyncio event loop is single-threaded and
        # there is no await between the dict ``get`` and ``pop`` below,
        # so no other task can interleave a competing modification.
        if not self.clients and self.session_id is not None:
            existing = _connect_channels.get(self.session_id)
            if existing is self:
                _connect_channels.pop(self.session_id, None)

    async def broadcast(self, sender: WebSocket, message: dict) -> None:
        dead: list[WebSocket] = []
        # Snapshot the client set so concurrent leave() calls don't
        # mutate it under iteration.
        for client in list(self.clients):
            if client is sender:
                continue
            try:
                await client.send_json(message)
            except Exception:
                dead.append(client)
        for d in dead:
            await self.leave(d)
        # Tell the survivors that a peer dropped so their UI flips out
        # of the "paired" state immediately instead of waiting for the
        # next reconnect tick (~30s under the browser's exponential
        # backoff).
        if dead and self.clients:
            notice = {
                "type": "peer_left",
                "peers": len(self.clients),
                "reason": PeerLeftReason.SEND_FAILED,
            }
            for client in list(self.clients):
                try:
                    await client.send_json(notice)
                except Exception:
                    pass


_connect_channels: dict[str, _ConnectChannel] = {}
_connect_channels_lock = asyncio.Lock()


async def _get_connect_channel(session_id: str) -> _ConnectChannel:
    async with _connect_channels_lock:
        chan = _connect_channels.get(session_id)
        if chan is None:
            chan = _ConnectChannel(session_id=session_id)
            _connect_channels[session_id] = chan
        return chan


@app.websocket("/ws/connect-bridge")
async def connect_bridge(ws: WebSocket) -> None:
    """Bidirectional bridge between the browser and the Connect desktop app.

    Both sides connect to this endpoint. The first message must be a
    ``hello`` frame carrying ``session_id`` and ``role`` so the server can
    route them onto the same channel.
    """
    await ws.accept()
    channel: _ConnectChannel | None = None
    role: str | None = None
    session_id: str | None = None
    try:
        # Wait for the hello frame before joining any channel.
        hello = await ws.receive_json()
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await ws.send_json({
                "type": "error",
                "code": ErrorCode.BAD_HELLO,
                "message": "first frame must be hello",
                "retriable": False,
            })
            await ws.close()
            return

        # Protocol version negotiation. A missing field means a pre-v1
        # client (the original Connect helper) — accept it as v0 so the
        # upgrade rollout doesn't lock anyone out. A version strictly
        # greater than what we speak means the server is older than the
        # client; close with version_mismatch so the helper can surface
        # an "update ToneForge" prompt instead of looping on rejections.
        raw_version = hello.get("protocol_version")
        try:
            client_version = int(raw_version) if raw_version is not None else 0
        except (TypeError, ValueError):
            client_version = 0
        if client_version > CONNECT_BRIDGE_PROTOCOL_VERSION:
            await ws.send_json({
                "type": "version_mismatch",
                "required": CONNECT_BRIDGE_PROTOCOL_VERSION,
                "client": client_version,
            })
            await ws.close()
            return

        session_id = str(hello.get("session_id") or "default")
        role = str(hello.get("role") or "unknown")

        # Acknowledge to v1+ clients so they can confirm the server
        # speaks their dialect before sending traffic. v0 clients
        # never sent protocol_version and don't expect hello_ack, so
        # we stay silent for them.
        if client_version >= 1:
            await ws.send_json({
                "type": "hello_ack",
                "protocol_version": CONNECT_BRIDGE_PROTOCOL_VERSION,
            })

        channel = await _get_connect_channel(session_id)
        await channel.join(ws)
        await ws.send_json({"type": "joined", "session_id": session_id, "peers": len(channel.clients) - 1})

        while True:
            try:
                msg = await asyncio.wait_for(
                    ws.receive_json(),
                    timeout=CONNECT_BRIDGE_RECV_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                # No traffic for the recv window. Probe the peer with a
                # ping. If the ping send itself fails the peer is already
                # gone — fall through to the outbound disconnect path.
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    break
                # Wait for *any* frame (a pong is the expected reply, but
                # an unsolicited frame is equally good proof of life).
                # A second timeout means the peer is dead.
                try:
                    msg = await asyncio.wait_for(
                        ws.receive_json(),
                        timeout=CONNECT_BRIDGE_PONG_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    # Heartbeat-detected drop. Tell the survivors before
                    # tearing down so their UI flips out of paired state
                    # without waiting for the next outbound frame.
                    notice = {
                        "type": "peer_left",
                        "peers": max(0, len(channel.clients) - 1),
                        "reason": PeerLeftReason.HEARTBEAT_TIMEOUT,
                    }
                    for client in list(channel.clients):
                        if client is ws:
                            continue
                        try:
                            await client.send_json(notice)
                        except Exception:
                            pass
                    break
                # If we got the expected pong, eat it and resume the
                # main wait. Any other frame falls through to dispatch
                # below — the peer self-identified as alive by sending
                # it, which is all we needed to know.
                if isinstance(msg, dict) and msg.get("type") == "pong":
                    continue
            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type")
            if mtype == "preset_push":
                preset = msg.get("preset")
                if isinstance(preset, dict):
                    channel.last_preset = preset
                    await channel.broadcast(ws, {"type": "preset_push", "preset": preset})
                rid = msg.get("request_id")
                if rid is not None:
                    await ws.send_json({"type": "ack", "request_id": rid})
            elif mtype == "ping":
                await ws.send_json({"type": "pong"})
            elif mtype == "set_gain":
                # Cache + broadcast. Clamp into [0, 1] so a misbehaving
                # client can't drive the helper's gain out of range.
                try:
                    gain = float(msg.get("gain", 0.0))
                except (TypeError, ValueError):
                    gain = 0.0
                gain = max(0.0, min(1.0, gain))
                channel.last_gain = gain
                await channel.broadcast(ws, {"type": "set_gain", "gain": gain})
            elif mtype == "apply_chain":
                # UI overrides auto-applied tone with a curated monitor
                # chain. The server resolves the id → spec here so
                # Connect doesn't carry a YAML parser, and so an invalid
                # id surfaces as an explicit error frame instead of a
                # silent passthrough to a confused helper.
                rid = msg.get("request_id")
                chain_id = msg.get("chain_id")
                if not isinstance(chain_id, str) or not chain_id:
                    await ws.send_json({
                        "type": "error",
                        "code": ErrorCode.CHAIN_ID_MISSING,
                        "message": "apply_chain frame requires a non-empty chain_id",
                        "retriable": False,
                    })
                    continue
                try:
                    chain = _load_monitor_chain(chain_id)
                except _ChainNotFoundError as exc:
                    await ws.send_json({
                        "type": "error",
                        "code": ErrorCode.CHAIN_NOT_FOUND,
                        "message": str(exc),
                        "retriable": False,
                    })
                    continue
                except _ChainSpecError as exc:
                    # A malformed YAML in the bundled bank is a
                    # ship-blocking bug. Surface it; don't broadcast.
                    logger.error(f"[connect-bridge] chain spec invalid for {chain_id}: {exc}")
                    await ws.send_json({
                        "type": "error",
                        "code": ErrorCode.CHAIN_SPEC_INVALID,
                        "message": str(exc),
                        "retriable": False,
                    })
                    continue
                spec = _monitor_chain_to_wire(chain)
                channel.last_chain = spec
                await channel.broadcast(ws, {
                    "type": "apply_chain",
                    "chain_id": spec["id"],
                    "chain": spec,
                })
                # Telemetry: positive label for the calibrator refit.
                # Best-effort — log failures must not break the apply path.
                try:
                    from tone_forge.tone.instrumentation import log_applied as _log_applied
                    _log_applied(
                        spec["id"],
                        session_id=getattr(channel, "session_id", None),
                    )
                except Exception as _apply_log_exc:
                    logger.warning(f"[connect-bridge] tone applied log failed: {_apply_log_exc}")
                if rid is not None:
                    await ws.send_json({"type": "ack", "request_id": rid})
            elif mtype == "connect_state":
                # v2: Connect → Browser engine snapshot. Cache the last
                # one so a late-joining JAM sees it on join. Broadcast
                # only — no server-side mutation of the payload, no
                # ack required (Connect emits on its own cadence).
                channel.last_connect_state = msg
                await channel.broadcast(ws, msg)
            elif mtype == "latency_report":
                # v2: Connect → Browser measured engine latency. Same
                # cache-then-broadcast pattern as connect_state. Low
                # rate (transition-driven, not periodic) so caching
                # is cheap.
                channel.last_latency_report = msg
                await channel.broadcast(ws, msg)
            elif mtype == "session_data":
                # v2: Browser → Connect song metadata. Cached so a
                # Connect helper that joins after JAM finishes
                # analysis still sees the current song. Future
                # consumer is an in-Connect SessionStore; today the
                # broadcast just lands at PresetBridge's no-op
                # callback.
                channel.last_session_data = msg
                await channel.broadcast(ws, msg)
            elif mtype == "transport_state" or mtype == "input_meter":
                # v2: high-rate frames. Broadcast but never cache
                # (stale-on-arrival; would mislead late joiners).
                await channel.broadcast(ws, msg)
            elif mtype == "measure_latency":
                # v2: Browser → Connect impulse-probe trigger. NOT
                # cached: it's a user-explicit one-shot request (the
                # probe plays an audible tone). Replaying it to a
                # late-joining Connect would re-fire the impulse,
                # which would be obnoxious. Pass through to live
                # peers only.
                await channel.broadcast(ws, msg)
            elif mtype == "load_stems":
                # v2: Browser → Connect stem-playback handoff.
                # Cached so a Connect helper that joins after the
                # current song was loaded still gets the URLs.
                # Payload is plain JSON (URLs only — the actual
                # audio bytes are served from /api/serve-file),
                # so caching is cheap.
                channel.last_load_stems = msg
                await channel.broadcast(ws, msg)
            else:
                # Pass-through for any other typed message (status, transport
                # commands, etc.) so we don't have to keep adding branches.
                await channel.broadcast(ws, msg)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[connect-bridge] {role or '?'}/{session_id or '?'} disconnected with error: {e}")
    finally:
        if channel is not None:
            await channel.leave(ws)


# ----- Audio-Ownership Pivot, Phase 6 -------------------------------
#
# Two endpoints supporting the "is Connect installed?" + "launch it
# now" affordance in JAM. Both are local-only — they do filesystem
# probes and macOS `open(1)` invocations, never anything network-
# facing or device-touching. Safety properties:
#   * Path arguments come exclusively from
#     ``connect_bridge.discover_connect_bundle()`` /
#     ``discover_connect_binary()``; nothing user-supplied reaches
#     subprocess.
#   * The launch endpoint shells out via ``subprocess.run([...])``
#     with a fixed argv (no ``shell=True``).
#   * Both endpoints succeed-with-empty-payload on non-macOS hosts
#     and on missing-bundle so jam.js can render the "Install
#     Connect" CTA without an error path.
#
# Wire shape:
#   GET /api/connect/installed
#     -> { "installed": bool,
#          "path":      str | None,
#          "version":   str | None }
#   POST /api/connect/launch
#     -> { "launched": bool,
#          "method":   "open_bundle" | "open_path" | "none" }


@app.get("/api/connect/installed")
async def connect_installed() -> JSONResponse:
    """Filesystem probe for the Connect.app bundle.

    No side effects. Cheap enough to call on every JAM page load.
    Returns `{installed: false, path: null, version: null}` when the
    bundle isn't where we expect — JAM uses that to surface an
    "Install Connect" link instead of the "Launch Connect" button.
    """
    try:
        from local_engine.connect_bridge import (
            discover_connect_bundle,
            read_connect_bundle_version,
        )
    except Exception as exc:  # pragma: no cover — import-time guard
        logger.warning(f"[connect-install] discover import failed: {exc}")
        return JSONResponse({
            "installed": False,
            "path":      None,
            "version":   None,
        })

    bundle = discover_connect_bundle()
    if bundle is None:
        return JSONResponse({
            "installed": False,
            "path":      None,
            "version":   None,
        })
    version = read_connect_bundle_version(bundle)
    return JSONResponse({
        "installed": True,
        "path":      str(bundle),
        "version":   version,
    })


@app.post("/api/connect/launch", status_code=202)
async def connect_launch() -> JSONResponse:
    """Best-effort launch of the Connect.app helper.

    Strategy (in order):
      1. ``open -b com.toneforge.connect`` — Launch Services lookup;
         works once the .app has been registered (i.e. ever launched
         since being moved to Applications).
      2. ``open <discovered path>`` — direct fallback for fresh
         installs that Launch Services hasn't indexed yet, or for
         dev builds living in the repo.
      3. ``"none"`` — bundle not found anywhere we look; JAM should
         show the install CTA.

    Returns immediately with HTTP 202. The caller polls the
    connect-bridge WebSocket for the actual paired state; this
    endpoint does NOT confirm Connect started successfully.
    """
    try:
        from local_engine.connect_bridge import (
            discover_connect_bundle,
            CONNECT_BUNDLE_ID,
        )
    except Exception as exc:  # pragma: no cover — import-time guard
        logger.warning(f"[connect-launch] discover import failed: {exc}")
        return JSONResponse(
            {"launched": False, "method": "none"}, status_code=202
        )

    # Step 1: try Launch Services lookup by bundle ID. Fast and the
    # canonical path once the user has dragged the .app into
    # /Applications and run it at least once.
    try:
        result = subprocess.run(
            ["open", "-b", CONNECT_BUNDLE_ID],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return JSONResponse(
                {"launched": True, "method": "open_bundle"}, status_code=202
            )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.info(f"[connect-launch] open -b failed: {exc}")
    except Exception as exc:  # pragma: no cover — surface but don't 500
        logger.warning(f"[connect-launch] open -b unexpected: {exc}")

    # Step 2: direct-path fallback. Only fire if we actually have a
    # discovered bundle; never pass an unvalidated path to open(1).
    bundle = discover_connect_bundle()
    if bundle is not None:
        try:
            result = subprocess.run(
                ["open", str(bundle)],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return JSONResponse(
                    {"launched": True, "method": "open_path"}, status_code=202
                )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.info(f"[connect-launch] open path failed: {exc}")
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[connect-launch] open path unexpected: {exc}")

    return JSONResponse(
        {"launched": False, "method": "none"}, status_code=202
    )


class ToneIgnoredRequest(BaseModel):
    """Payload for `/api/tone/ignored`.

    `chain_id` is the chain the user dismissed. `reason` is a short
    UX-path identifier (`"card_closed"`, `"song_switched"`,
    `"other_chain_applied"`). `session_id` and `source_url` are
    optional join keys for the calibration refit.
    """

    chain_id: str
    reason: Optional[str] = None
    session_id: Optional[str] = None
    source_url: Optional[str] = None


@app.post("/api/tone/ignored")
async def tone_ignored_endpoint(request: ToneIgnoredRequest) -> JSONResponse:
    """Record a tone-card dismissal as a negative label.

    Telemetry-only; never returns a hard failure. The log writer
    already swallows exceptions, but we double-wrap here so a missing
    instrumentation module (e.g., partial install) doesn't 500 the
    Jam UI.
    """
    try:
        from tone_forge.tone.instrumentation import log_ignored as _log_ignored
        _log_ignored(
            request.chain_id,
            session_id=request.session_id,
            source_url=request.source_url,
            reason=request.reason,
        )
    except Exception as exc:
        logger.warning(f"[tone] /api/tone/ignored log failed: {exc}")
    return JSONResponse(content={"ok": True})


_ACCEPTED_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aiff", ".aif", ".webm"}

# Maximum duration for waveform preview (5 minutes)
_MAX_PREVIEW_DURATION = 300


@app.post("/api/preview-waveform")
async def preview_waveform_endpoint(
    file: UploadFile = File(...),
) -> JSONResponse:
    """Generate waveform peaks for preview (fast, no analysis).

    Returns waveform data for visualization before analysis.
    """
    import librosa

    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix}. Accepted: {sorted(_ACCEPTED_SUFFIXES)}.",
        )

    # Write to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        # Load audio at 22050 Hz (fast, mono)
        y, sr = librosa.load(str(tmp_path), sr=22050, mono=True, duration=_MAX_PREVIEW_DURATION)
        duration_sec = len(y) / sr

        # Compute waveform peaks
        waveform_data = _compute_waveform_peaks(y, num_points=1000)
        waveform_data["sample_rate"] = int(sr)
        waveform_data["duration_sec"] = duration_sec
        waveform_data["filename"] = file.filename

        return JSONResponse(content=_convert_numpy_types(waveform_data))
    finally:
        tmp_path.unlink(missing_ok=True)


class PreviewUrlRequest(BaseModel):
    url: str


@app.post("/api/preview-waveform-url")
async def preview_waveform_url_endpoint(request: PreviewUrlRequest) -> JSONResponse:
    """Generate waveform peaks from YouTube URL (fast preview).

    Downloads full audio (up to 5 min) and returns waveform data.
    """
    import librosa

    if not _check_yt_dlp():
        raise HTTPException(
            status_code=400,
            detail="yt-dlp is not installed. Install with: pip install yt-dlp",
        )

    url = request.url

    with tempfile.TemporaryDirectory(prefix="toneforge_preview_") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)

        try:
            # Download full audio (up to 5 min) for preview
            audio_path, start_timestamp, display_name = _download_youtube_audio(
                url, tmp_dir_path, duration=_MAX_PREVIEW_DURATION
            )

            # Load audio
            y, sr = librosa.load(str(audio_path), sr=22050, mono=True, duration=_MAX_PREVIEW_DURATION)
            duration_sec = len(y) / sr

            # Compute waveform peaks
            waveform_data = _compute_waveform_peaks(y, num_points=1000)
            waveform_data["sample_rate"] = int(sr)
            waveform_data["duration_sec"] = duration_sec
            waveform_data["filename"] = display_name
            waveform_data["start_timestamp"] = start_timestamp

            return JSONResponse(content=_convert_numpy_types(waveform_data))

        except Exception as e:
            logger.error(f"Preview URL failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze")
async def analyze_endpoint(
    file: UploadFile = File(...),
    source_kind: str = "auto",  # Auto-detect by default
    platform: str = "auto",
    extract_midi: bool = True,  # Extract MIDI by default
    analysis_mode: str = "studio",  # quick, studio, or deep
) -> JSONResponse:
    """Analyze an uploaded audio clip using unified pipeline.

    Auto-detects:
    - Whether it's a full mix or isolated instrument
    - Whether it's guitar, synth, or other

    Returns recommendations for all platforms (Helix, Pedals, Synth).
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix}. Accepted: {sorted(_ACCEPTED_SUFFIXES)}.",
        )

    # Write to a temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    # Build unified pipeline config based on analysis_mode
    if analysis_mode.lower() == "quick":
        config = PipelineConfig.fast()
    elif analysis_mode.lower() == "deep":
        config = PipelineConfig.deep()
    else:
        config = PipelineConfig.standard()

    # Apply options
    config.extract_midi = extract_midi
    config.source_name = file.filename

    try:
        # Use unified pipeline
        pipeline = get_unified_pipeline()
        result = await pipeline.analyze(tmp_path, config)

        # Convert to response dict
        response = result.to_dict()
        response["filename"] = file.filename
        response["source_kind"] = source_kind
        response["analysis_mode"] = config.mode.value

        # Add to history
        history_entry = _add_to_history({
            "name": file.filename or "Uploaded file",
            "detected_type": response.get("detected_type", "guitar"),
            "summary": response.get("detection", {}).get("summary", ""),
            "duration": response.get("duration_sec"),
        }, full_result=response)

        response["history_id"] = history_entry["id"]

        return JSONResponse(_convert_numpy_types(response))

    except Exception as e:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/analyze-stream")
async def analyze_stream_endpoint(
    file: UploadFile = File(...),
    source_kind: str = Form("auto"),
    platform: str = Form("auto"),
    extract_midi: str = Form("true"),  # Form data is string
    fast_mode: str = Form("true"),  # Set to "false" for deep analysis with stem separation
    analysis_mode: str = Form("studio"),  # quick, studio, or deep
    start_time: Optional[float] = Form(None),  # Trim start in seconds
    end_time: Optional[float] = Form(None),  # Trim end in seconds
) -> StreamingResponse:
    """Analyze with SSE progress streaming using unified pipeline.

    Args:
        fast_mode: If "true" (default), skip stem separation for speed.
                   If "false", perform deep analysis with stem separation and MIDI extraction.
        analysis_mode: Quality mode - "quick" (fast preview), "studio" (balanced),
                       or "deep" (maximum quality). Default: studio.
        start_time: Optional trim start in seconds
        end_time: Optional trim end in seconds
    """
    # Convert string form params to booleans
    extract_midi_bool = extract_midi.lower() not in ("false", "0", "no")
    fast_mode_bool = fast_mode.lower() not in ("false", "0", "no")

    # Build unified pipeline config
    if fast_mode_bool:
        config = PipelineConfig.fast()
    elif analysis_mode.lower() == "deep":
        config = PipelineConfig.deep()
    else:
        config = PipelineConfig.standard()

    # Apply options
    config.extract_midi = extract_midi_bool
    config.trim_start = start_time
    config.trim_end = end_time
    config.source_name = file.filename

    async def generate():
        def send_event(event_type: str, data: dict):
            return f"data: {json.dumps({'type': event_type, **data})}\n\n"

        suffix = Path(file.filename or "").suffix.lower()
        if suffix and suffix not in _ACCEPTED_SUFFIXES:
            yield send_event("error", {"message": f"Unsupported file type {suffix}"})
            return

        yield send_event("progress", {"message": "Uploading file...", "percent": 5})
        await asyncio.sleep(0)

        # Write to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = Path(tmp.name)

        try:
            # Use unified pipeline
            pipeline = get_unified_pipeline()

            async for event in pipeline.analyze_streaming(tmp_path, config):
                if isinstance(event, ProgressEvent):
                    yield send_event("progress", {
                        "message": event.message,
                        "percent": event.percent,
                    })
                    await asyncio.sleep(0)
                elif isinstance(event, AnalysisResult):
                    # Convert to response dict
                    response = event.to_dict()
                    response["filename"] = file.filename
                    response["source_kind"] = source_kind
                    response["analysis_mode"] = config.mode.value
                    response["deep_analysis"] = config.mode == UnifiedAnalysisMode.DEEP

                    # Add to history
                    history_entry = _add_to_history({
                        "name": file.filename or "Uploaded file",
                        "detected_type": response.get("detected_type", "guitar"),
                        "summary": response.get("detection", {}).get("summary", ""),
                        "duration": response.get("duration_sec"),
                    }, full_result=response)

                    response["history_id"] = history_entry["id"]

                    yield send_event("progress", {"message": "Analysis complete", "percent": 100})
                    await asyncio.sleep(0)
                    yield send_event("result", {"data": _convert_numpy_types(response)})

        except Exception as e:
            logger.exception("Stream analysis failed")
            yield send_event("error", {"message": str(e)})
        finally:
            tmp_path.unlink(missing_ok=True)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _generate_synth_hints(desc) -> list[str]:
    """Generate tweak hints for synth sounds."""
    hints = []

    if desc.oscillator.type == "saw":
        hints.append("Start with a sawtooth oscillator for this buzzy, harmonically-rich tone.")
    elif desc.oscillator.type == "square":
        hints.append("Use a square/pulse wave oscillator for this hollow, woody character.")
    elif desc.oscillator.type == "sine":
        hints.append("A pure sine wave will get you close to this smooth, fundamental-heavy tone.")

    if desc.oscillator.num_voices > 1:
        hints.append(f"Add unison with {desc.oscillator.num_voices} voices and ~{desc.oscillator.detune:.0f} cents detune for width.")

    if desc.filter.cutoff_normalized < 0.7:
        hints.append(f"Low-pass filter around {desc.filter.cutoff_hz:.0f}Hz gives this muffled character.")

    if desc.filter.resonance > 0.3:
        hints.append("Add some filter resonance for that characteristic 'quack'.")

    if desc.amp_envelope.attack_ms > 50:
        hints.append(f"Slow attack (~{desc.amp_envelope.attack_ms:.0f}ms) creates the pad-like swell.")
    elif desc.amp_envelope.attack_ms < 10:
        hints.append("Keep attack very short for punchy, percussive response.")

    if desc.lfo and desc.lfo.rate_hz > 0:
        hints.append(f"LFO at ~{desc.lfo.rate_hz:.1f}Hz modulating {desc.lfo.target} creates the movement.")

    if desc.has_chorus:
        hints.append("Chorus effect adds the stereo width and shimmer.")

    if desc.has_reverb:
        hints.append("Add reverb for the ambient, spacious quality.")

    return hints


def _generate_bass_hints(desc) -> list[str]:
    """Generate tweak hints for bass sounds."""
    hints = []

    amp_family = desc.amp.family
    if amp_family == "ampeg_svt":
        hints.append("Classic Ampeg SVT tone - try driving the preamp for that signature growl.")
    elif amp_family == "darkglass":
        hints.append("Modern Darkglass tone - blend clean and dirty for clarity with grit.")
    elif amp_family == "fender_bassman":
        hints.append("Fender Bassman provides clean headroom with vintage sparkle.")

    if desc.amp.gain > 0.5:
        hints.append(f"Significant overdrive detected ({desc.amp.gain:.0%}) - consider a dedicated bass drive pedal.")

    if desc.technique == "slap":
        hints.append("Slap technique detected - boost high mids (~2-3kHz) for pop and cut lows for tightness.")
    elif desc.technique == "pick":
        hints.append("Pick attack detected - a slight mid boost brings out the percussive quality.")
    elif desc.technique == "fretless":
        hints.append("Fretless character - emphasize mids for that 'mwah' and consider subtle chorus.")

    if desc.effects.compressor > 0.4:
        hints.append("Heavy compression detected - try a bass compressor with slow attack to preserve transients.")

    if desc.effects.octaver > 0.3:
        hints.append("Sub-octave detected - an octave pedal like the Boss OC-3 or EHX POG will recreate this.")

    return hints


def _generate_drum_hints(desc) -> list[str]:
    """Generate tweak hints for drum sounds."""
    hints = []

    machine = desc.matched_machine
    if machine == "tr808":
        hints.append("TR-808 style - long kick decay, snappy snares, sizzly hats.")
    elif machine == "tr909":
        hints.append("TR-909 style - punchy kicks with attack, crisp sample-based hats.")
    elif machine == "sp1200":
        hints.append("SP-1200 style - crunchy lo-fi character from 12-bit sampling.")

    if desc.kick.sub_presence > 0.6:
        hints.append(f"Heavy sub-bass in the kick - tune around {desc.kick.pitch_hz:.0f}Hz for maximum impact.")

    if desc.snare.noise > 0.6:
        hints.append("Snare has lots of noise/sizzle - real snare wires or white noise layering.")

    if desc.overall.compression > 0.5:
        hints.append("Drums are heavily compressed - parallel compression or a bus compressor.")

    if desc.overall.swing > 0.15:
        hints.append(f"Swing detected (~{desc.overall.swing:.0%}) - adjust your sequencer's swing/groove.")

    if desc.overall.tempo_bpm > 0:
        hints.append(f"Tempo estimated at ~{desc.overall.tempo_bpm:.0f} BPM.")

    return hints


def _bass_descriptor_to_dict(desc) -> dict:
    """Convert BassDescriptor to dict for JSON response."""
    return {
        "source": {
            "kind": desc.source.kind,
            "duration_sec": float(desc.source.duration_sec),
            "sample_rate": int(desc.source.sample_rate),
            "filename": desc.source.filename,
        },
        "technique": desc.technique,
        "amp": {
            "family": desc.amp.family,
            "gain": float(desc.amp.gain),
            "voicing": {
                "bass": float(desc.amp.voicing.bass),
                "low_mid": float(desc.amp.voicing.low_mid),
                "mid": float(desc.amp.voicing.mid),
                "treble": float(desc.amp.voicing.treble),
            },
            "alternates": desc.amp.alternates,
        },
        "cab": {
            "configuration": desc.cab.configuration,
            "speaker_size": desc.cab.speaker_size,
            "character": desc.cab.character,
        },
        "effects": {
            "compressor": float(desc.effects.compressor),
            "overdrive": float(desc.effects.overdrive),
            "chorus": float(desc.effects.chorus),
            "octaver": float(desc.effects.octaver),
            "envelope_filter": float(desc.effects.envelope_filter),
        },
        "confidence": {
            "amp_family": float(desc.confidence.amp_family),
            "gain": float(desc.confidence.gain),
            "cab": float(desc.confidence.cab),
            "technique": float(desc.confidence.technique),
        },
    }


def _drum_descriptor_to_dict(desc) -> dict:
    """Convert DrumDescriptor to dict for JSON response."""
    return {
        "source": {
            "kind": desc.source.kind,
            "duration_sec": float(desc.source.duration_sec),
            "sample_rate": int(desc.source.sample_rate),
            "filename": desc.source.filename,
        },
        "kick": {
            "pitch_hz": float(desc.kick.pitch_hz),
            "decay_ms": float(desc.kick.decay_ms),
            "saturation": float(desc.kick.saturation),
            "sub_presence": float(desc.kick.sub_presence),
            "click": float(desc.kick.click),
        },
        "snare": {
            "pitch_hz": float(desc.snare.pitch_hz),
            "noise": float(desc.snare.noise),
            "snap": float(desc.snare.snap),
            "decay_ms": float(desc.snare.decay_ms),
            "body": float(desc.snare.body),
        },
        "hihat": {
            "open_ratio": float(desc.hihat.open_ratio),
            "open_closed_ratio": float(desc.hihat.open_ratio),  # Alias for frontend
            "decay_ms": float(desc.hihat.decay_ms),
            "brightness": float(desc.hihat.brightness),
            "sizzle": float(desc.hihat.sizzle),
        },
        "overall": {
            "tempo_bpm": float(desc.overall.tempo_bpm),
            "swing": float(desc.overall.swing),
            "compression": float(desc.overall.compression),
            "saturation": float(desc.overall.saturation),
            "style": desc.overall.style,
        },
        # Top-level aliases for frontend compatibility
        "tempo_bpm": float(desc.overall.tempo_bpm),
        "swing": float(desc.overall.swing),
        "compression": float(desc.overall.compression),
        "matched_machine": desc.matched_machine,
        "confidence": {
            "tempo": float(desc.confidence.tempo),
            "style": float(desc.confidence.style),
            "kick": float(desc.confidence.kick),
            "snare": float(desc.confidence.snare),
        },
    }


def _get_bass_recommendations(desc) -> list[dict]:
    """Get bass gear recommendations based on descriptor."""
    import json
    bass_blocks_path = Path(__file__).parent / "data" / "bass_blocks.json"
    if not bass_blocks_path.exists():
        return []

    with open(bass_blocks_path) as f:
        catalog = json.load(f)

    recommendations = []
    amp_family = desc.amp.family

    # Find matching amp
    for amp in catalog.get("amps", []):
        if amp_family in amp.get("families", []):
            recommendations.append({
                "slot": "amp",
                "category": "amp",
                "display": amp["display"],
                "models": amp.get("models", ""),
                "rationale": f"Matches detected {amp_family.replace('_', ' ')} tone",
                "price_estimate": amp.get("price_estimate", ""),
                "params": {
                    "gain": float(round(float(desc.amp.gain) * 10, 1)),
                    "bass": float(round(float(desc.amp.voicing.bass) * 10, 1)),
                    "mid": float(round(float(desc.amp.voicing.mid) * 10, 1)),
                    "treble": float(round(float(desc.amp.voicing.treble) * 10, 1)),
                },
            })
            break

    # Add cab recommendation
    for cab in catalog.get("cabs", []):
        if cab.get("config") == desc.cab.configuration or cab.get("character") == desc.cab.character:
            recommendations.append({
                "slot": "cab",
                "category": "cab",
                "display": cab["display"],
                "models": cab.get("models", ""),
                "rationale": f"{desc.cab.configuration} configuration with {desc.cab.character} character",
                "price_estimate": cab.get("price_estimate", ""),
            })
            break

    # Add drive if overdrive detected
    if float(desc.effects.overdrive) > 0.2:
        for drive in catalog.get("drives", []):
            recommendations.append({
                "slot": "drive",
                "category": "drive",
                "display": drive["display"],
                "models": drive.get("models", ""),
                "rationale": "Adds grit and definition to your bass tone",
                "price_estimate": drive.get("price_estimate", ""),
            })
            break

    # Add compressor if compression detected
    if float(desc.effects.compressor) > 0.3:
        for comp in catalog.get("compressors", []):
            recommendations.append({
                "slot": "compressor",
                "category": "compressor",
                "display": comp["display"],
                "models": comp.get("models", ""),
                "rationale": "Tightens dynamics and adds sustain",
                "price_estimate": comp.get("price_estimate", ""),
            })
            break

    return recommendations


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/studio")
async def admin_page():
    """Serve the admin UI."""
    return FileResponse("static/studio.html")


@app.get("/api/admin/serve-file")
async def admin_serve_file(
    path: str = Query(..., description="Path to the file to serve"),
    download: bool = Query(False, description="Force download instead of streaming"),
):
    """Serve stem files or other generated files for playback/download.

    Security: Only serves files from allowed directories (temp dirs, demucs output).
    """
    from pathlib import Path
    import os

    file_path = Path(path)

    # Security: only allow serving from specific directories
    allowed_prefixes = [
        "/tmp/",
        "/private/tmp/",  # macOS /tmp symlink target
        "/var/folders/",  # macOS temp
        "/private/var/folders/",  # macOS /var symlink target
        str(Path.home() / ".cache"),
        str(Path.home() / ".toneforge"),  # ToneForge cache dir
    ]

    # Check both resolved and original path (symlinks can cause issues)
    path_str = str(file_path.resolve())
    path_str_orig = str(file_path)
    if not any(path_str.startswith(prefix) or path_str_orig.startswith(prefix) for prefix in allowed_prefixes):
        logger.warning(f"Serve-file blocked: {path_str} (orig: {path_str_orig})")
        raise HTTPException(status_code=403, detail="Access denied: file not in allowed directory")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    # Determine content type
    suffix = file_path.suffix.lower()
    content_types = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".mid": "audio/midi",
        ".midi": "audio/midi",
    }
    content_type = content_types.get(suffix, "application/octet-stream")

    if download:
        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type="application/octet-stream",  # Force download, not playback
            headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
        )
    else:
        return FileResponse(
            path=str(file_path),
            media_type=content_type,
        )


@app.post("/api/admin/analyze-quality")
async def admin_analyze_quality(
    file: UploadFile = File(...),
    quick: bool = False,
    start_time: Optional[float] = Form(None),
    end_time: Optional[float] = Form(None),
) -> JSONResponse:
    """Deep quality analysis for admin view.

    Returns detailed stem quality metrics, confidence maps,
    MIDI pass statistics, and archetype information.
    """
    logger.info(f"Admin analyze-quality: received file {file.filename}")
    suffix = Path(file.filename or "").suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    logger.info(f"Admin analyze-quality: saved to {tmp_path}, size={tmp_path.stat().st_size} bytes")

    try:
        import librosa
        import time as time_module

        load_start = time_module.time()
        logger.info("Admin analyze-quality: loading audio...")

        # Load audio
        audio, sr = librosa.load(str(tmp_path), sr=22050, mono=True)

        # Apply trim if specified
        if start_time is not None or end_time is not None:
            total_duration = len(audio) / sr
            start_sample = int((start_time or 0) * sr)
            end_sample = int((end_time or total_duration) * sr)
            start_sample = max(0, min(start_sample, len(audio)))
            end_sample = max(start_sample, min(end_sample, len(audio)))
            audio = audio[start_sample:end_sample]
            logger.info(f"Admin analyze-quality: trimmed to {len(audio)/sr:.1f}s")
        load_time = time_module.time() - load_start
        logger.info(f"Admin analyze-quality: loaded {len(audio)/sr:.1f}s audio in {load_time*1000:.0f}ms")

        result = {
            "filename": file.filename,
            "duration_sec": len(audio) / sr,
            "sample_rate": sr,
            "load_time_ms": load_time * 1000,
            "reconstruction_available": _RECONSTRUCTION_AVAILABLE,
        }

        # Initialize analysis to None in case reconstruction isn't available
        analysis = None

        if _RECONSTRUCTION_AVAILABLE:
            analysis_start = time_module.time()
            logger.info("Admin analyze-quality: starting reconstruction analysis...")

            # Run reconstruction analysis
            if quick:
                config = ReconstructionConfig.fast()
                config.extract_midi = False
            else:
                config = ReconstructionConfig(
                    extract_midi=False,
                    analyze_continuity=False,
                )
            pipeline = get_pipeline(config)
            logger.info("Admin analyze-quality: running analyze_only...")
            analysis, quality_report = pipeline.analyze_only(
                audio=audio,
                sr=sr,
                stem_type="guitar",
            )

            analysis_time = time_module.time() - analysis_start

            # Stem Quality Details
            if analysis.stem_quality:
                sq = analysis.stem_quality
                result["stem_quality"] = {
                    "overall_quality": getattr(sq, 'overall_quality', None),
                    "contamination_score": getattr(sq, 'contamination_score', None),
                    "transient_integrity": getattr(sq, 'transient_integrity', None),
                    "harmonic_purity": getattr(sq, 'harmonic_purity', None),
                    "reverb_density": getattr(sq, 'reverb_density', None),
                    "stereo_coherence": getattr(sq, 'stereo_coherence', None),
                    "snr_estimate": getattr(sq, 'snr_estimate', None),
                }

            # Contamination Details
            if analysis.contamination:
                ct = analysis.contamination
                result["contamination"] = {
                    "overall_contamination": getattr(ct, 'overall_contamination', None),
                    "bass_bleed": getattr(ct, 'bass_bleed', None),
                    "drum_bleed": getattr(ct, 'drum_bleed', None),
                    "vocal_bleed": getattr(ct, 'vocal_bleed', None),
                    "reverb_contamination": getattr(ct, 'reverb_contamination', None),
                }

            # Artifact Details
            if analysis.artifacts:
                af = analysis.artifacts
                result["artifacts"] = {
                    "clipping_detected": getattr(af, 'clipping_detected', None),
                    "clipping_severity": getattr(af, 'clipping_severity', None),
                    "noise_floor_db": getattr(af, 'noise_floor_db', None),
                    "dc_offset": getattr(af, 'dc_offset', None),
                    "phase_issues": getattr(af, 'phase_issues', None),
                }

            # Role Classification
            if analysis.role:
                role = analysis.role
                result["role"] = {
                    "primary_role": getattr(role, 'primary_role', None),
                    "confidence": getattr(role, 'confidence', None),
                    "spectral_profile": getattr(role, 'spectral_profile', None),
                    "temporal_profile": getattr(role, 'temporal_profile', None),
                }

            # Confidence Map Summary
            if analysis.confidence_map:
                cm = analysis.confidence_map
                result["confidence_map"] = {
                    "global_confidence": getattr(cm, 'global_confidence', None),
                    "region_count": getattr(cm, 'region_count', None),
                    "low_confidence_regions": getattr(cm, 'low_confidence_region_count', 0),
                    "high_confidence_regions": getattr(cm, 'high_confidence_region_count', 0),
                }

            # Continuity Analysis
            if analysis.continuity:
                cont = analysis.continuity
                result["continuity"] = {
                    "sustained_regions": getattr(cont, 'sustained_region_count', None),
                    "avg_sustain_duration": getattr(cont, 'avg_sustain_duration', None),
                    "pitch_stability": getattr(cont, 'pitch_stability', None),
                }

            # Archetype Priors
            if analysis.priors:
                priors = analysis.priors
                result["priors"] = {
                    "source_archetype": getattr(priors, 'source_archetype', None),
                    "onset_threshold": getattr(priors, 'suggested_onset_threshold', None),
                    "frame_threshold": getattr(priors, 'suggested_frame_threshold', None),
                    "min_note_ms": getattr(priors, 'min_note_ms', None),
                    "quantization_strength": getattr(priors, 'quantization_strength', None),
                }

            # Quality Report
            if quality_report:
                result["quality_report"] = {
                    "overall_confidence": quality_report.overall_confidence,
                    "quality_level": quality_report.overall_quality.value if hasattr(quality_report.overall_quality, 'value') else str(quality_report.overall_quality),
                    "should_proceed": quality_report.should_proceed,
                    "warning_count": len(quality_report.warnings),
                    "warnings": [
                        {
                            "level": w.level.value if hasattr(w.level, 'value') else str(w.level),
                            "category": w.category.value if hasattr(w.category, 'value') else str(w.category),
                            "message": w.message,
                            "recommendation": w.recommendation,
                        }
                        for w in quality_report.warnings
                    ],
                }

            result["analysis_time_ms"] = analysis_time * 1000

        # Also run standard analysis to get confidence scores
        descriptor = analyzer.analyze(
            str(tmp_path),
            source_kind="isolated_guitar",
            stem_quality=analysis.stem_quality if analysis else None,
            contamination=analysis.contamination if analysis else None,
        )

        result["confidence_scores"] = {
            "amp_family": descriptor.confidence.amp_family,
            "gain": descriptor.confidence.gain,
            "cab": descriptor.confidence.cab,
            "effects": descriptor.confidence.effects,
        }

        result["detected"] = {
            "amp_family": descriptor.amp.family,
            "gain": descriptor.amp.gain,
        }

        return JSONResponse(_convert_numpy_types(result))

    except Exception as e:
        logger.exception("Admin quality analysis failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/admin/analyze-deep")
async def admin_analyze_deep(
    file: UploadFile = File(...),
    start_time: Optional[float] = Form(None),
    end_time: Optional[float] = Form(None),
) -> StreamingResponse:
    """Deep GPU-powered analysis with live progress streaming via SSE.

    NOTE: Disabled by default to prevent server blocking. Use local GPU engine instead.
    Set TONEFORGE_ALLOW_SERVER_DEEP=1 to enable for local development.
    """
    # Check if server-side deep analysis is allowed (disabled by default for production)
    if not os.environ.get("TONEFORGE_ALLOW_SERVER_DEEP"):
        return JSONResponse(
            status_code=400,
            content={
                "detail": "Deep analysis requires local GPU engine. Start the local engine or select Quick/Standard mode.",
                "error_code": "LOCAL_ENGINE_REQUIRED"
            }
        )
    import asyncio
    import time
    from concurrent.futures import ThreadPoolExecutor

    # Capture trim params for closure
    trim_start = start_time
    trim_end = end_time

    suffix = Path(file.filename or "").suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    # Thread pool for CPU/GPU bound operations
    executor = ThreadPoolExecutor(max_workers=1)

    async def event_stream():
        """Generate SSE events during processing."""
        loop = asyncio.get_event_loop()

        def sse_event(event_type: str, data: dict) -> str:
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        try:
            yield sse_event("start", {
                "filename": file.filename,
                "file_size": tmp_path.stat().st_size,
                "timestamp": datetime.now().isoformat(),
            })
            await asyncio.sleep(0)  # Flush

            # Timing accumulator for profiling
            stage_timings = {}
            pipeline_start = time.time()

            # Stage 1: Load audio
            stage_start = time.time()
            yield sse_event("progress", {
                "stage": "loading",
                "message": "Loading audio file...",
                "percent": 5,
            })
            await asyncio.sleep(0)

            import librosa

            def load_audio():
                return librosa.load(str(tmp_path), sr=22050, mono=True)

            audio, sr = await loop.run_in_executor(executor, load_audio)
            duration_sec = len(audio) / sr

            # Apply trim if specified
            if trim_start is not None or trim_end is not None:
                import soundfile as sf

                total_duration = len(audio) / sr
                start_sample = int((trim_start or 0) * sr)
                end_sample = int((trim_end or total_duration) * sr)
                start_sample = max(0, min(start_sample, len(audio)))
                end_sample = max(start_sample, min(end_sample, len(audio)))
                audio = audio[start_sample:end_sample]
                duration_sec = len(audio) / sr

                # Write trimmed audio back for stem separation
                sf.write(str(tmp_path), audio, sr)

            stage_timings["loading"] = {"duration_ms": (time.time() - stage_start) * 1000}

            # Compute waveform peaks for visualization
            waveform_data = _compute_waveform_peaks(audio, num_points=1000)
            waveform_data["sample_rate"] = int(sr)
            waveform_data["duration_sec"] = duration_sec

            yield sse_event("progress", {
                "stage": "loading",
                "message": f"Loaded {duration_sec:.1f}s audio",
                "percent": 10,
                "duration_sec": duration_sec,
                "stage_duration_ms": stage_timings["loading"]["duration_ms"],
            })
            await asyncio.sleep(0)

            # Stage 2: Stem separation (GPU)
            stage_start = time.time()
            yield sse_event("progress", {
                "stage": "stem_separation",
                "message": "Starting stem separation (GPU)...",
                "percent": 15,
            })
            await asyncio.sleep(0)

            stem_result = None
            try:
                from tone_forge.stem_separator import separate_all_stems, is_available
                if is_available():
                    yield sse_event("progress", {
                        "stage": "stem_separation",
                        "message": "Running Demucs model on GPU...",
                        "percent": 20,
                    })
                    await asyncio.sleep(0)

                    def run_stem_sep():
                        # Use 6-stem model for better separation (guitar, piano, other, drums, bass, vocals)
                        return separate_all_stems(tmp_path, model_name="htdemucs_6s")

                    stem_paths = await loop.run_in_executor(executor, run_stem_sep)
                    stem_result = {name: str(path) for name, path in stem_paths.items()}
                    stage_timings["stem_separation"] = {
                        "duration_ms": (time.time() - stage_start) * 1000,
                        "gpu_used": True,
                    }

                    yield sse_event("progress", {
                        "stage": "stem_separation",
                        "message": f"Separated {len(stem_paths)} stems",
                        "percent": 40,
                        "stems": list(stem_paths.keys()),
                        "stage_duration_ms": stage_timings["stem_separation"]["duration_ms"],
                    })
                    await asyncio.sleep(0)
                else:
                    stage_timings["stem_separation"] = {"duration_ms": 0, "skipped": True}
                    yield sse_event("progress", {
                        "stage": "stem_separation",
                        "message": "Demucs not available, skipping stem separation",
                        "percent": 40,
                        "skipped": True,
                    })
                    await asyncio.sleep(0)
            except Exception as e:
                stage_timings["stem_separation"] = {
                    "duration_ms": (time.time() - stage_start) * 1000,
                    "error": str(e),
                }
                yield sse_event("progress", {
                    "stage": "stem_separation",
                    "message": f"Stem separation failed: {e}",
                    "percent": 40,
                    "error": str(e),
                })
                await asyncio.sleep(0)

            # Stage 3: Quality analysis (broken into sub-steps for progress)
            quality_result = {}
            analysis_results = {}
            quality_report = None

            # Load guitar stem if available for better quality analysis
            guitar_audio = audio  # Default to original mix
            guitar_sr = sr
            if stem_result and "guitar" in stem_result:
                yield sse_event("progress", {
                    "stage": "loading_stem",
                    "message": "Loading separated guitar stem for analysis...",
                    "percent": 42,
                })
                await asyncio.sleep(0)

                try:
                    def load_guitar_stem():
                        return librosa.load(stem_result["guitar"], sr=22050, mono=True)
                    guitar_audio, guitar_sr = await loop.run_in_executor(executor, load_guitar_stem)
                except Exception as e:
                    logger.warning(f"Failed to load guitar stem, using original mix: {e}")

            if _RECONSTRUCTION_AVAILABLE:
                from tone_forge.reconstruction import (
                    get_analyzer as get_stem_analyzer,
                    get_detector as get_contamination_detector,
                    get_artifact_detector,
                    get_confidence_mapper,
                    get_role_classifier,
                    get_continuity_analyzer,
                    get_quality_reporter,
                )

                # Step 3a: Stem quality
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "stem_quality",
                    "message": "Analyzing stem quality...",
                    "percent": 45,
                })
                await asyncio.sleep(0)

                try:
                    def run_stem_quality():
                        return get_stem_analyzer().analyze(guitar_audio, guitar_sr, "guitar")
                    analysis_results["stem_quality"] = await loop.run_in_executor(executor, run_stem_quality)
                    stage_timings["stem_quality"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Stem quality failed: {e}")
                    stage_timings["stem_quality"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3b: Contamination
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "contamination",
                    "message": "Detecting contamination...",
                    "percent": 48,
                })
                await asyncio.sleep(0)

                try:
                    def run_contamination():
                        return get_contamination_detector().detect(guitar_audio, guitar_sr, "guitar")
                    analysis_results["contamination"] = await loop.run_in_executor(executor, run_contamination)
                    stage_timings["contamination"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Contamination detection failed: {e}")
                    stage_timings["contamination"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3c: Artifacts
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "artifacts",
                    "message": "Detecting artifacts...",
                    "percent": 50,
                })
                await asyncio.sleep(0)

                try:
                    def run_artifacts():
                        return get_artifact_detector().detect(guitar_audio, guitar_sr, "guitar")
                    analysis_results["artifacts"] = await loop.run_in_executor(executor, run_artifacts)
                    stage_timings["artifacts"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Artifact detection failed: {e}")
                    stage_timings["artifacts"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3d: Role classification
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "role",
                    "message": "Classifying role...",
                    "percent": 52,
                })
                await asyncio.sleep(0)

                try:
                    def run_role():
                        return get_role_classifier().classify(guitar_audio, guitar_sr, "guitar")
                    analysis_results["role"] = await loop.run_in_executor(executor, run_role)
                    stage_timings["role"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Role classification failed: {e}")
                    stage_timings["role"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3e: Continuity analysis (the slow one)
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "continuity",
                    "message": "Analyzing temporal continuity...",
                    "percent": 54,
                })
                await asyncio.sleep(0)

                try:
                    def run_continuity():
                        return get_continuity_analyzer().analyze(guitar_audio, guitar_sr)
                    analysis_results["continuity"] = await loop.run_in_executor(executor, run_continuity)
                    stage_timings["continuity"] = {"duration_ms": (time.time() - stage_start) * 1000}

                    yield sse_event("progress", {
                        "stage": "continuity",
                        "message": "Continuity analysis complete",
                        "percent": 58,
                        "stage_duration_ms": stage_timings["continuity"]["duration_ms"],
                    })
                    await asyncio.sleep(0)
                except Exception as e:
                    logger.warning(f"Continuity analysis failed: {e}")
                    stage_timings["continuity"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}
                    yield sse_event("progress", {
                        "stage": "continuity",
                        "message": f"Continuity analysis failed: {e}",
                        "percent": 58,
                        "error": str(e),
                    })
                    await asyncio.sleep(0)

                # Step 3f: Confidence map
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "confidence_map",
                    "message": "Building confidence map...",
                    "percent": 59,
                })
                await asyncio.sleep(0)

                try:
                    def run_confidence_map():
                        return get_confidence_mapper().build_map(
                            audio, sr, "guitar",
                            stem_quality=analysis_results.get("stem_quality"),
                            contamination=analysis_results.get("contamination"),
                            artifacts=analysis_results.get("artifacts"),
                        )
                    analysis_results["confidence_map"] = await loop.run_in_executor(executor, run_confidence_map)
                    stage_timings["confidence_map"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Confidence map failed: {e}")
                    stage_timings["confidence_map"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Step 3g: Quality report
                stage_start = time.time()
                yield sse_event("progress", {
                    "stage": "quality_report",
                    "message": "Generating quality report...",
                    "percent": 60,
                })
                await asyncio.sleep(0)

                try:
                    reporter = get_quality_reporter()
                    quality_report = reporter.generate_report(
                        stem_type="guitar",
                        stem_quality=analysis_results.get("stem_quality"),
                        contamination=analysis_results.get("contamination"),
                        artifacts=analysis_results.get("artifacts"),
                        confidence_map=analysis_results.get("confidence_map"),
                    )
                    stage_timings["quality_report"] = {"duration_ms": (time.time() - stage_start) * 1000}
                except Exception as e:
                    logger.warning(f"Quality report failed: {e}")
                    stage_timings["quality_report"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}

                # Collect results
                if analysis_results.get("stem_quality"):
                    sq = analysis_results["stem_quality"]
                    quality_result["stem_quality"] = {
                        "overall_quality": getattr(sq, 'overall_quality', None),
                        "contamination_score": getattr(sq, 'contamination_score', None),
                        "transient_integrity": getattr(sq, 'transient_integrity', None),
                        "harmonic_purity": getattr(sq, 'harmonic_purity', None),
                    }

                if analysis_results.get("contamination"):
                    ct = analysis_results["contamination"]
                    # Extract contamination regions for waveform overlay
                    contamination_regions = []
                    for e in getattr(ct, 'events', []):
                        contamination_regions.append({
                            "type": e.contamination_type.value if hasattr(e.contamination_type, 'value') else str(e.contamination_type),
                            "start": e.time_start,
                            "end": e.time_end,
                            "severity": e.severity,
                            "confidence": e.confidence,
                            "source": e.source_stem,
                        })
                    quality_result["contamination"] = {
                        "overall_score": getattr(ct, 'overall_contamination', None),
                        "drum_bleed": getattr(ct, 'drum_bleed', None),
                        "vocal_bleed": getattr(ct, 'vocal_bleed', None),
                        "regions": contamination_regions,  # For waveform overlay
                    }

                if analysis_results.get("continuity"):
                    cont = analysis_results["continuity"]
                    quality_result["continuity"] = {
                        "sustained_ratio": getattr(cont, 'sustained_ratio', None),
                        "average_stability": getattr(cont, 'average_stability', None),
                        "phrase_count": getattr(cont, 'phrase_count', None),
                        "dominant_envelope": getattr(cont, 'dominant_envelope', None),
                    }
                    if hasattr(quality_result["continuity"]["dominant_envelope"], 'value'):
                        quality_result["continuity"]["dominant_envelope"] = quality_result["continuity"]["dominant_envelope"].value

                if analysis_results.get("artifacts"):
                    art = analysis_results["artifacts"]
                    # Extract artifact regions for waveform overlay
                    artifact_regions = []
                    for a in getattr(art, 'artifacts', []):
                        artifact_regions.append({
                            "type": a.artifact_type.value if hasattr(a.artifact_type, 'value') else str(a.artifact_type),
                            "start": a.time_start,
                            "end": a.time_end,
                            "severity": a.severity,
                            "confidence": a.confidence,
                        })
                    quality_result["artifacts"] = {
                        "overall_score": getattr(art, 'overall_artifact_score', None),
                        "artifact_count": getattr(art, 'artifact_count', None),
                        "clipping_detected": any(
                            a.artifact_type.value == "clipping"
                            for a in getattr(art, 'artifacts', [])
                        ),
                        "noise_floor_db": None,  # Not directly available
                        "dc_offset": None,
                        "phase_issues": any(
                            a.artifact_type.value == "phase_artifact"
                            for a in getattr(art, 'artifacts', [])
                        ),
                        "regions": artifact_regions,  # For waveform overlay
                    }

                if analysis_results.get("role"):
                    role = analysis_results["role"]
                    quality_result["role"] = {
                        "primary_role": role.primary_role.value if hasattr(role.primary_role, 'value') else str(role.primary_role),
                        "confidence": role.confidence,
                        "spectral_profile": role.spectral_profile.value if hasattr(role.spectral_profile, 'value') else str(role.spectral_profile),
                        "temporal_profile": role.temporal_profile.value if hasattr(role.temporal_profile, 'value') else str(role.temporal_profile),
                    }

                if quality_report:
                    quality_result["quality_report"] = {
                        "overall_quality": quality_report.overall_quality,
                        "overall_confidence": quality_report.overall_confidence,
                        "should_proceed": quality_report.should_proceed,
                        "warning_count": quality_report.total_warnings,
                        "warnings": [
                            {
                                "level": w.level.value if hasattr(w.level, 'value') else str(w.level),
                                "category": w.category.value if hasattr(w.category, 'value') else str(w.category),
                                "message": w.message,
                                "recommendation": w.recommendation,
                            }
                            for w in quality_report.warnings
                        ],
                    }

            # Create a mock analysis object for downstream use
            class AnalysisHolder:
                pass
            analysis = AnalysisHolder()
            analysis.stem_quality = analysis_results.get("stem_quality")
            analysis.contamination = analysis_results.get("contamination")

            # Stage 4: Per-stem MIDI extraction (GPU)
            # Extract MIDI from each separated stem for cleaner results
            stage_start = time.time()
            yield sse_event("progress", {
                "stage": "midi_extraction",
                "message": "Starting per-stem MIDI extraction...",
                "percent": 65,
            })
            await asyncio.sleep(0)

            midi_result = {}
            midi_stems = {}
            try:
                from tone_forge import midi_extractor

                # Define which stems to extract MIDI from and their display names
                # (stem_key, display_label, is_drums, stem_type_for_profile)
                stem_configs = [
                    ("drums", "Drums", True, "drums"),
                    ("bass", "Bass", False, "bass"),
                    ("guitar", "Guitar", False, "lead"),
                    ("piano", "Keys", False, "pad"),
                    ("other", "Synth", False, "synth"),
                    ("vocals", "Vocals", False, "vocals"),
                ]

                if stem_result:
                    # Extract MIDI from each available stem
                    available_stems = [cfg for cfg in stem_configs if cfg[0] in stem_result]
                    total_stems = len(available_stems)

                    for idx, (stem_key, stem_label, is_drums, stem_type) in enumerate(available_stems):
                        progress = 65 + int(((idx + 1) / total_stems) * 20)  # 65-85%
                        yield sse_event("progress", {
                            "stage": "midi_extraction",
                            "message": f"Extracting {stem_label} MIDI ({idx + 1}/{total_stems})...",
                            "percent": progress,
                        })
                        await asyncio.sleep(0)

                        stem_path = stem_result[stem_key]
                        display_name = file.filename or 'Track'

                        try:
                            if is_drums:
                                def extract_fn(path=stem_path, name=display_name, label=stem_label):
                                    return midi_extractor.extract_drum_midi(
                                        str(path),
                                        preset_name=f"{name} - {label}",
                                    )
                            else:
                                def extract_fn(path=stem_path, name=display_name, label=stem_label, stype=stem_type):
                                    return midi_extractor.extract_midi(
                                        str(path),
                                        preset_name=f"{name} - {label}",
                                        stem_type=stype,
                                    )

                            stem_midi = await loop.run_in_executor(executor, extract_fn)

                            # Only include stems that have notes
                            if stem_midi.note_count > 0:
                                stem_midi_data = {
                                    "label": stem_label,
                                    "filename": stem_midi.filename,
                                    "content": stem_midi.content,
                                    "note_count": stem_midi.note_count,
                                    "duration_seconds": stem_midi.duration_seconds,
                                    # Per-stem extractor tempo (renamed from
                                    # ``tempo_bpm`` to disambiguate from the
                                    # session-canonical tempo at the top of
                                    # the result payload). See the matching
                                    # rename in tone_forge/midi/gpu_extractor.py.
                                    "extraction_tempo_bpm": stem_midi.tempo_bpm,
                                    "pitch_range": {
                                        "lowest": int(stem_midi.pitch_range[0]),
                                        "highest": int(stem_midi.pitch_range[1]),
                                    },
                                }
                                if stem_midi.provenance:
                                    stem_midi_data["provenance"] = stem_midi.provenance
                                midi_stems[stem_key] = stem_midi_data
                                logger.info(f"{stem_label} MIDI: {stem_midi.note_count} notes")
                        except Exception as e:
                            logger.warning(f"MIDI extraction failed for {stem_label}: {e}")

                    # Summary
                    total_notes = sum(s.get("note_count", 0) for s in midi_stems.values())
                    stem_summary = ", ".join(f"{v['label']}" for v in midi_stems.values())
                    yield sse_event("progress", {
                        "stage": "midi_extraction",
                        "message": f"MIDI extracted: {total_notes} notes ({stem_summary})",
                        "percent": 85,
                    })
                    await asyncio.sleep(0)

                    # Set midi_result for backward compatibility (priority: guitar > piano > other > bass)
                    for stem_key in ["guitar", "piano", "other", "bass", "vocals"]:
                        if stem_key in midi_stems:
                            midi_result = midi_stems[stem_key]
                            break

                stage_timings["midi_extraction"] = {"duration_ms": (time.time() - stage_start) * 1000, "gpu_used": True}

            except Exception as e:
                stage_timings["midi_extraction"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}
                yield sse_event("progress", {
                    "stage": "midi_extraction",
                    "message": f"MIDI extraction error: {e}",
                    "percent": 85,
                    "error": str(e),
                })
                await asyncio.sleep(0)

            # Stage 5: Tone analysis
            stage_start = time.time()
            # Use separated guitar stem if available for better accuracy
            tone_audio_path = stem_result.get("guitar", str(tmp_path)) if stem_result else str(tmp_path)
            tone_source_kind = "isolated_guitar" if stem_result and "guitar" in stem_result else "full_mix"

            yield sse_event("progress", {
                "stage": "tone_analysis",
                "message": f"Running tone analysis on {'guitar stem' if tone_source_kind == 'isolated_guitar' else 'original mix'}...",
                "percent": 90,
            })
            await asyncio.sleep(0)

            tone_result = {}
            try:
                def run_tone_analysis():
                    return analyzer.analyze(
                        tone_audio_path,
                        source_kind=tone_source_kind,
                        stem_quality=analysis.stem_quality if analysis else None,
                        contamination=analysis.contamination if analysis else None,
                        capture_reasoning=True,  # Enable explainability
                    )

                descriptor = await loop.run_in_executor(executor, run_tone_analysis)

                tone_result = {
                    "amp_family": descriptor.amp.family,
                    "gain": descriptor.amp.gain,
                    "amp": {
                        "family": descriptor.amp.family,
                        "gain": descriptor.amp.gain,
                        "voicing": {
                            "bass": descriptor.amp.voicing.bass,
                            "mid": descriptor.amp.voicing.mid,
                            "treble": descriptor.amp.voicing.treble,
                            "presence": descriptor.amp.voicing.presence,
                            "mid_scoop": descriptor.amp.voicing.mid_scoop,
                        },
                        "alternates": descriptor.amp.alternates,
                    },
                    "cab": {
                        "configuration": descriptor.cab.configuration,
                        "speaker_character": descriptor.cab.speaker_character,
                        "mic_position": descriptor.cab.mic_position,
                    },
                    "effects": {
                        "overdrive": {
                            "style": descriptor.effects.overdrive_pedal.style if descriptor.effects.overdrive_pedal else None,
                            "drive": descriptor.effects.overdrive_pedal.drive if descriptor.effects.overdrive_pedal else 0,
                        } if descriptor.effects.overdrive_pedal else None,
                        "compressor": {
                            "amount": descriptor.effects.compressor.amount if descriptor.effects.compressor else 0,
                            "character": descriptor.effects.compressor.character if descriptor.effects.compressor else None,
                        } if descriptor.effects.compressor else None,
                        "modulation": {
                            "type": descriptor.effects.modulation.type if descriptor.effects.modulation else "none",
                            "rate": descriptor.effects.modulation.rate if descriptor.effects.modulation else 0,
                            "depth": descriptor.effects.modulation.depth if descriptor.effects.modulation else 0,
                        } if descriptor.effects.modulation else None,
                        "delay": {
                            "type": descriptor.effects.delay.type if descriptor.effects.delay else "none",
                            "time_ms": descriptor.effects.delay.time_ms if descriptor.effects.delay else 0,
                            "feedback": descriptor.effects.delay.feedback if descriptor.effects.delay else 0,
                            "mix": descriptor.effects.delay.mix if descriptor.effects.delay else 0,
                        } if descriptor.effects.delay else None,
                        "reverb": {
                            "type": descriptor.effects.reverb.type if descriptor.effects.reverb else "none",
                            "size": descriptor.effects.reverb.size if descriptor.effects.reverb else 0,
                            "mix": descriptor.effects.reverb.mix if descriptor.effects.reverb else 0,
                        } if descriptor.effects.reverb else None,
                    },
                    "guitar": {
                        "pickup_brightness": descriptor.guitar.pickup_brightness,
                        "playing_style": descriptor.guitar.playing_style,
                        "estimated_tuning": descriptor.guitar.estimated_tuning,
                    },
                    "confidence": {
                        "amp_family": descriptor.confidence.amp_family,
                        "gain": descriptor.confidence.gain,
                        "cab": descriptor.confidence.cab,
                        "effects": descriptor.confidence.effects,
                    },
                    "reasoning": descriptor.reasoning.to_dict() if descriptor.reasoning else None,
                }
                stage_timings["tone_analysis"] = {"duration_ms": (time.time() - stage_start) * 1000}
            except Exception as e:
                stage_timings["tone_analysis"] = {"duration_ms": (time.time() - stage_start) * 1000, "error": str(e)}
                yield sse_event("progress", {
                    "stage": "tone_analysis",
                    "message": f"Tone analysis error: {e}",
                    "percent": 95,
                    "error": str(e),
                })
                await asyncio.sleep(0)

            # Complete
            total_time = time.time() - pipeline_start

            # Calculate audio processing ratio
            audio_ratio = total_time / duration_sec if duration_sec > 0 else 0

            # Build final response
            final_response = _convert_numpy_types({
                "success": True,
                "total_time_sec": total_time,
                "duration_sec": duration_sec,
                "sample_rate": sr,
                "filename": file.filename or "Unknown",
                "stems": stem_result,
                "quality": quality_result,
                "midi": midi_result,
                "midi_stems": midi_stems,  # Per-stem MIDI for export
                "tone": tone_result,
                "waveform": waveform_data,
                "profiling": {
                    "total_ms": total_time * 1000,
                    "audio_duration_sec": duration_sec,
                    "processing_ratio": audio_ratio,  # time to process / audio duration
                    "stages": stage_timings,
                },
            })

            # Save to history
            history_entry = _add_to_history({
                "name": file.filename or "Admin upload",
                "detected_type": "guitar",
                "summary": f"Deep analysis: {tone_result.get('amp_family', 'unknown')} amp",
                "amp_family": tone_result.get("amp_family"),
                "gain": tone_result.get("gain"),
                "duration": duration_sec,
                "deep_analysis": True,
                "has_quality_data": bool(quality_result),
                "has_reasoning": bool(tone_result.get("reasoning")),
            }, full_result=final_response)

            # Add history ID and admin URL to response
            final_response["history_id"] = history_entry["id"]
            final_response["admin_url"] = f"/studio?analysis={history_entry['id']}"

            yield sse_event("complete", final_response)

        except Exception as e:
            logger.exception("Deep analysis failed")
            yield sse_event("error", {
                "message": str(e),
            })
        finally:
            tmp_path.unlink(missing_ok=True)
            executor.shutdown(wait=False)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
async def get_history(
    q: Optional[str] = Query(None, description="Search query"),
    limit: int = Query(50, ge=1, le=100),
) -> JSONResponse:
    """Get analysis history, optionally filtered by search query."""
    history = _load_history()

    if q:
        q_lower = q.lower()
        history = [
            entry for entry in history
            if q_lower in entry.get("name", "").lower()
            or q_lower in entry.get("detected_type", "").lower()
            or q_lower in entry.get("summary", "").lower()
            or q_lower in (entry.get("amp_family") or "").lower()
        ]

    # Convert numpy types and handle inf/nan values
    return JSONResponse(_convert_numpy_types({"history": history[:limit]}))


@app.delete("/api/history")
async def clear_history() -> JSONResponse:
    """Clear all history."""
    _save_history([])
    return JSONResponse({"status": "cleared"})


@app.delete("/api/history/{entry_id}")
async def delete_history_entry(entry_id: str) -> JSONResponse:
    """Delete a specific history entry."""
    history = _load_history()
    history = [e for e in history if e.get("id") != entry_id]
    _save_history(history)
    return JSONResponse({"status": "deleted"})


@app.post("/api/history/save")
async def save_to_history(request: Request) -> JSONResponse:
    """Save a local engine result to history for admin access."""
    data = await request.json()
    filename = data.get("filename", "Unknown")
    result = data.get("result", {})

    # Extract metadata from the result
    detected_type = result.get("detected_type", "unknown")
    detection = result.get("detection", {})
    guitar = result.get("guitar", {})
    descriptor = guitar.get("descriptor", {}) if guitar else {}

    entry = _add_to_history({
        "filename": filename,
        "detected_type": detected_type,
        "summary": detection.get("summary", ""),
        "amp_family": descriptor.get("amp", {}).get("family") if descriptor else None,
        "gain": descriptor.get("amp", {}).get("gain") if descriptor else None,
        "duration": descriptor.get("source", {}).get("duration_sec") if descriptor else None,
        "source": "local_engine",
    }, full_result=result)

    return JSONResponse({"id": entry["id"], "status": "saved"})


@app.get("/api/history/{entry_id}")
async def get_history_entry(entry_id: str) -> JSONResponse:
    """Get a specific history entry with full result."""
    entry = _get_history_item(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="History entry not found")
    # Convert numpy types and handle inf/nan values
    return JSONResponse(_convert_numpy_types(entry))


@app.get("/api/session/{entry_id}")
async def get_session_bundle(entry_id: str) -> JSONResponse:
    """Return the Jam-shaped ``SessionBundle`` for a persisted analysis.

    Studio continues to read ``/api/history/{id}`` and the legacy
    ``AnalysisResult.to_dict()`` shape; Jam consumes this route. The
    bundle assembler is in ``tone_forge.session.bundle``; the route
    handler is a thin lookup-and-translate that stays inside the API
    composition layer so the session/ subsystem keeps its boundary
    discipline.
    """
    entry = _get_history_item(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="History entry not found")
    result = entry.get("result")
    if not isinstance(result, dict):
        # Entry exists but the full analysis blob was never persisted.
        # Surface as 422 so the Jam client knows the row is unusable.
        raise HTTPException(
            status_code=422,
            detail="History entry has no analysis result",
        )
    device_caps = _device_caps_for_session()
    tone_match = _retrieve_tone_for_history(result, device_caps=device_caps)
    bundle = build_session_bundle(
        result,
        session_id=entry_id,
        tone_match=tone_match,
        device_caps=device_caps,
    )
    payload = serialize_session_bundle(bundle)

    # Legacy sidecar fields. The bundle contract is intentionally narrow
    # (Priority-5); the Jam UI also reads a few legacy AnalysisResult
    # fields that the streaming Studio path produces directly. Surface
    # them here as ``legacy_*`` keys so the deep-link adapter on the
    # client can pass them through unchanged. Without this, refreshing
    # /jam/:id loses tone recommendations, preset_matches, and
    # top-level tempo_bpm — they exist in the persisted history row
    # but never made it onto the bundle.
    payload["legacy_tone"] = result.get("tone")
    payload["legacy_preset_matches"] = result.get("preset_matches") or {}
    payload["legacy_tempo_bpm"] = result.get("tempo_bpm")
    payload["legacy_detected_key"] = result.get("detected_key")

    return JSONResponse(_convert_numpy_types(payload))


def _device_caps_for_session():
    """Hydrate ``DeviceCaps`` from the persisted onboarding answer.

    Composition layer for the devices/ subsystem. Reads
    ``device.json`` and projects the user's selected ``DeviceClass``
    into ``DeviceCaps``. Returns ``None`` when nothing is persisted
    so ``session.bundle.build`` falls back to its interface-only
    default. Never raises — a corrupted preferences file already
    causes ``load_preferences`` to return ``None`` per its docstring.
    """
    try:
        prefs = _load_device_preferences()
    except Exception as exc:
        logger.warning(f"[devices] load_preferences failed: {exc}")
        return None
    return _caps_from_preferences(prefs)


class DevicePreferencesRequest(BaseModel):
    """Payload for ``POST /api/device/preferences``.

    Mirrors the ``DevicePreferences`` contract but accepts the enum
    values as raw strings so the browser doesn't need to know the
    Python enum surface. ``first_seen_iso`` / ``last_used_iso`` are
    not accepted from the client — the persistence layer stamps them.
    """

    device_class: str
    audio_input_name: Optional[str] = None
    preferred_chain_family: Optional[str] = None
    # Sparkle auto-update opt-in (§3C). ``None`` = "no expressed
    # preference" (Sparkle's built-in default applies). ``True`` /
    # ``False`` are explicit writes; when the value differs from the
    # persisted record, POST also broadcasts ``set_auto_update`` to
    # every active Connect peer so the change takes effect without
    # waiting for a restart.
    auto_update_enabled: Optional[bool] = None


def _serialize_device_preferences(prefs: Optional[_DevicePreferences]) -> Optional[dict]:
    """Wire shape for `/api/device/preferences` responses.

    Returns ``None`` when nothing is persisted so the browser can
    short-circuit to the onboarding screen with a single check.
    """
    if prefs is None:
        return None
    return {
        "device_class": prefs.device_class.value,
        "audio_input_name": prefs.audio_input_name,
        "preferred_chain_family": (
            prefs.preferred_chain_family.value
            if prefs.preferred_chain_family is not None
            else None
        ),
        "first_seen_iso": prefs.first_seen_iso,
        "last_used_iso": prefs.last_used_iso,
        "auto_update_enabled": prefs.auto_update_enabled,
    }


async def _broadcast_set_auto_update(enabled: bool) -> None:
    """Notify every active Connect peer that the user toggled the
    Sparkle auto-update preference.

    The preference is global (lives in ``device.json``), not
    per-session, so the broadcast fans out across every active
    ``_ConnectChannel`` in the registry. The frame is fire-and-forget
    from the server's perspective: Connect side writes
    ``UserDefaults`` on receipt; Sparkle picks the value up on its
    next scheduled-check tick. Failed sends are tolerated — the
    next channel ``join()`` replays the value from disk anyway.
    """
    frame = {
        "v": 1,
        "type": "set_auto_update",
        "enabled": enabled,
        "replayed": False,
    }
    # Snapshot the channels under the registry lock so a concurrent
    # join/leave doesn't mutate the dict while we iterate. We hold
    # the lock only long enough to copy the values — the actual
    # sends happen outside to avoid head-of-line blocking.
    async with _connect_channels_lock:
        channels = list(_connect_channels.values())
    for chan in channels:
        for client in list(chan.clients):
            try:
                await client.send_json(frame)
            except Exception:
                # Don't propagate per-client failures; the channel's
                # own dead-client reaping path will catch the socket
                # on its next broadcast.
                pass


@app.get("/api/device/preferences")
async def get_device_preferences_endpoint() -> JSONResponse:
    """Return the persisted onboarding answer, or ``null`` if absent.

    The Jam UI checks this on startup. ``null`` means "show the
    onboarding screen". A populated payload means "the user already
    answered; use it to seed ``DeviceCaps``".
    """
    try:
        prefs = _load_device_preferences()
    except Exception as exc:
        logger.warning(f"[devices] GET /api/device/preferences failed: {exc}")
        prefs = None
    return JSONResponse(content=_serialize_device_preferences(prefs))


@app.post("/api/device/preferences")
async def set_device_preferences_endpoint(
    request: DevicePreferencesRequest,
) -> JSONResponse:
    """Persist the user's onboarding answer.

    Returns 400 on unknown ``device_class`` / ``preferred_chain_family``
    values so the UI sees a fast error rather than silently writing
    a record that ``load_preferences`` will later reject. Returns the
    stamped canonical record on success so the client doesn't need to
    re-GET to see the assigned timestamps.
    """
    try:
        device_class = _DeviceClass(request.device_class)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown device_class: {request.device_class!r}",
        )

    family: Optional[_MonitorChainFamily]
    if request.preferred_chain_family is None:
        family = None
    else:
        try:
            family = _MonitorChainFamily(request.preferred_chain_family)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unknown preferred_chain_family: "
                    f"{request.preferred_chain_family!r}"
                ),
            )

    # Preserve first_seen_iso if a record already exists.
    existing = _load_device_preferences()
    first_seen = existing.first_seen_iso if existing is not None else None
    prev_auto_update = (
        existing.auto_update_enabled if existing is not None else None
    )

    prefs = _DevicePreferences(
        device_class=device_class,
        audio_input_name=request.audio_input_name,
        preferred_chain_family=family,
        first_seen_iso=first_seen,
        last_used_iso=None,  # save_preferences stamps this
        auto_update_enabled=request.auto_update_enabled,
    )
    stamped = _save_device_preferences(prefs)

    # Broadcast only when the auto-update preference actually changed
    # AND has a concrete bool value. A no-op POST (e.g. the UI saving
    # other fields without touching the toggle) must not spray
    # ``set_auto_update`` frames across every Connect peer.
    if (
        stamped.auto_update_enabled is not None
        and stamped.auto_update_enabled != prev_auto_update
    ):
        await _broadcast_set_auto_update(stamped.auto_update_enabled)

    return JSONResponse(content=_serialize_device_preferences(stamped))


@app.delete("/api/device/preferences")
async def delete_device_preferences_endpoint() -> JSONResponse:
    """Forget the onboarding answer (re-prompt on next session).

    The Jam UI exposes this as 'Reset device choice' in settings.
    Idempotent: deleting a non-existent file is not an error.
    """
    _clear_device_preferences()
    return JSONResponse(content={"ok": True})


def _serialize_audio_device_info(info: Optional[_AudioDeviceInfo]) -> Optional[dict]:
    """Wire shape for one CoreAudio device in the probe response."""
    if info is None:
        return None
    return {
        "device_id": info.device_id,
        "name": info.name,
        "input_channels": info.input_channels,
        "output_channels": info.output_channels,
    }


def _serialize_device_probe(probe: _DeviceProbe) -> dict:
    """Wire shape for ``GET /api/device/probe``.

    The probe contract guarantees never-raise; if the binary is
    missing or the JSON malformed, the probe still returns a
    ``DeviceProbe`` with ``probe_succeeded=False``. The serializer
    just projects the dataclass into JSON-friendly shape — the UI
    decides how to render based on ``probe_succeeded``.
    """
    return {
        "devices": [_serialize_audio_device_info(d) for d in probe.devices],
        "suggested_input": _serialize_audio_device_info(probe.suggested_input),
        "vendor_hint": probe.vendor_hint,
        "probe_succeeded": probe.probe_succeeded,
        "error_message": probe.error_message,
    }


@app.get("/api/device/probe")
async def get_device_probe_endpoint() -> JSONResponse:
    """Run the CoreAudio probe and return the result.

    Telemetry / hint surface for the onboarding modal. The probe
    itself never raises (contract in devices/discovery.py). The
    try/except below is a belt-and-braces second line of defense
    so a future contract violation never 500s the modal — the UI
    treats an empty / failed probe the same as "no hint", and the
    user falls back to the manual radio answer.
    """
    try:
        probe_result = _device_probe()
    except Exception as exc:  # pragma: no cover - probe() should not raise
        logger.warning(f"[devices] probe() raised unexpectedly: {exc}")
        probe_result = _DeviceProbe(
            devices=tuple(),
            suggested_input=None,
            vendor_hint=None,
            probe_succeeded=False,
            error_message=f"probe raised: {type(exc).__name__}",
        )
    return JSONResponse(content=_serialize_device_probe(probe_result))


def _retrieve_tone_for_history(
    result: dict,
    *,
    device_caps: Optional[_DeviceCaps] = None,
) -> Optional["ToneMatch"]:
    """Project legacy ``preset_matches`` into a tier-aware ``ToneMatch``.

    Composition layer for the tone/ subsystem. Reads the stem-keyed
    preset_matches blob, picks the entry that best matches the user's
    role, builds a minimal ``SongUnderstanding`` from the legacy tempo
    / key fields so the fallback policy can route on it, then hands
    everything to ``tone.retrieve()``. Returns ``None`` (and the
    bundle falls back to its conservative UNKNOWN path) if the legacy
    blob is shaped in a way the cleaner cannot use.

    ``device_caps``, when supplied, carries the user's persisted
    Discovery answer; its ``preferred_chain_family`` is forwarded to
    ``tone.retrieve`` so the LOW / UNKNOWN fallback chain id honors
    the user's explicit pin rather than the tempo / key heuristic.
    """
    from tone_forge.contracts import SongUnderstanding

    # Resolve the user's role the same way the bundle does so the
    # tone_match we compute matches the bundle's bundle.user_role.
    role_value = result.get("detected_type") or result.get("type")
    role = _UserRole.GUITAR
    if isinstance(role_value, str):
        normalized = role_value.strip().lower()
        if normalized in (r.value for r in _UserRole):
            role = _UserRole(normalized)

    matches = result.get("preset_matches")
    candidates: list = []
    if isinstance(matches, dict):
        # Prefer the role-keyed entry; fall back to stem-name variants
        # (``guitar_left`` / ``guitar_right``) since the production
        # pipeline writes stem names, not role names.
        preferred_keys = [role.value]
        if role == _UserRole.GUITAR:
            preferred_keys += ["guitar_left", "guitar_right"]
        for key in preferred_keys:
            block = matches.get(key)
            if isinstance(block, dict):
                candidates.append(block)

    # Minimal SongUnderstanding for fallback policy. We only need
    # tempo + key; the bundle assembler does the full reconstruction.
    tempo_bpm = 0.0
    for path in (("descriptor", "tempo"), ("descriptor", "tempo_bpm")):
        cur: Any = result
        for k in path:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(k)
        if isinstance(cur, (int, float)) and float(cur) > 0:
            tempo_bpm = float(cur)
            break
    key_raw = None
    desc = result.get("descriptor")
    if isinstance(desc, dict):
        key_raw = desc.get("key") if isinstance(desc.get("key"), str) else None
    understanding = SongUnderstanding(
        tempo_bpm=tempo_bpm,
        tempo_confidence=0.5 if tempo_bpm else 0.0,
        time_signature=(4, 4),
        beats_s=(),
        downbeats_s=(),
        sections=(),
        chords=(),
        key=key_raw,
        key_confidence=0.5 if key_raw else 0.0,
    )

    preferred_family = device_caps.preferred_chain_family if device_caps else None
    return tone_retrieval.retrieve(
        candidates,
        understanding=understanding,
        role=role,
        preferred_family=preferred_family,
    )


def _check_yt_dlp() -> bool:
    """Check if yt-dlp is available."""
    import sys
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


@app.get("/api/capabilities")
async def capabilities() -> dict:
    """Return server capabilities, including supported platforms and features."""
    from tone_forge import stem_separator

    return {
        "version": app.version,
        "stem_separation": stem_separator.is_available(),
        "youtube_support": _check_yt_dlp(),
        "supported_source_kinds": ["isolated_guitar", "stem_separated", "full_mix", "synth"],
        "supported_platforms": SUPPORTED_PLATFORMS,
        "platform_info": {
            "helix": "Line 6 Helix/HX Stomp/POD Go block recommendations",
            "pedals": "Real pedal and amp recommendations with prices",
            "synth": "Synth parameter analysis and recreation hints",
        },
        "analysis_modes": ["quick", "studio", "deep"],
        "note": (
            "full_mix requires stem_separation=true. "
            "If false, install with: pip install demucs torch torchaudio"
        ) if not stem_separator.is_available() else None,
    }


@app.get("/api/analysis-modes")
async def get_analysis_modes() -> dict:
    """Return available analysis modes with their configurations.

    Frontend can use this to display mode selection UI.
    """
    modes = {}
    for mode in AnalysisMode:
        config = get_analysis_config(mode)
        modes[mode.value] = {
            **describe_mode(mode),
            "config": {
                "stem_separation": config.stem_separation.enabled,
                "multi_pass_midi": config.midi_extraction.enable_multi_pass,
                "spectral_validation": config.midi_extraction.enable_spectral_validation,
                "quality_metrics": config.midi_extraction.enable_quality_metrics,
            },
        }

    return {
        "modes": modes,
        "default": "studio",
    }


class UrlAnalyzeRequest(BaseModel):
    url: str
    source_kind: str = "auto"  # Changed default to auto
    platform: str = "auto"
    fast_mode: bool = True  # Skip stem separation for speed (default: fast)
    analysis_mode: str = "studio"  # quick, studio, or deep
    extract_midi: bool = True  # Extract MIDI notes from audio
    start_time: Optional[float] = None  # Trim start in seconds
    end_time: Optional[float] = None  # Trim end in seconds
    use_local_engine: bool = False  # Proxy processing to local engine for GPU acceleration


class ExportRequest(BaseModel):
    chain: list[dict] = []
    descriptor: dict = {}
    format: str = "hlx"
    preset_name: str = "Tone Forge Export"
    # For bass/drums exports
    recommendations: list[dict] = []
    machine_match: dict = None
    # For Ableton Live Set export (full analysis result)
    full_result: dict = None
    # For MIDI export (stored from analysis)
    midi_data: dict = None


class MIDIExtractRequest(BaseModel):
    """Request for extracting MIDI from audio file path or stored data."""
    preset_name: str = "Extracted MIDI"
    min_note_duration_ms: float = 50
    velocity_sensitivity: float = 1.0
    quantize_to: int = None  # e.g., 16 for 16th notes


@app.post("/api/export")
async def export_preset(request: ExportRequest) -> JSONResponse:
    """Export signal chain to a downloadable preset format.

    Supported formats:
    - hlx: Line 6 Helix preset
    - json: Generic JSON
    - neural_dsp: Neural DSP Quad Cortex
    - synth_serum: Synth preset (Serum-style)
    - synth_vital: Synth preset (Vital-style)
    """
    from tone_forge import preset_export

    format_type = request.format.lower()

    try:
        if format_type == "hlx":
            result = preset_export.export_helix_preset(
                request.chain,
                None,  # We don't need full descriptor for Helix
                request.preset_name,
            )
        elif format_type == "hlx_stomp":
            result = preset_export.export_hx_stomp_preset(
                request.chain,
                None,
                request.preset_name,
            )
        elif format_type == "json":
            result = preset_export.export_json_preset(
                request.chain,
                request.descriptor,
                request.preset_name,
            )
        elif format_type == "neural_dsp":
            result = preset_export.export_neural_dsp_preset(
                request.chain,
                request.descriptor,
                request.preset_name,
            )
        elif format_type.startswith("synth"):
            target = format_type.replace("synth_", "") or "serum"
            result = preset_export.export_synth_preset(
                request.descriptor,
                request.preset_name,
                target,
            )
        elif format_type == "bass":
            result = preset_export.export_bass_preset(
                request.descriptor,
                request.recommendations or request.chain,
                request.preset_name,
            )
        elif format_type == "drums":
            result = preset_export.export_drums_preset(
                request.descriptor,
                request.machine_match,
                request.preset_name,
            )
        elif format_type == "ableton":
            result = preset_export.export_ableton_preset(
                request.descriptor,
                request.chain,
                request.preset_name,
                instrument_type="guitar",
            )
        elif format_type == "ableton_synth":
            result = preset_export.export_ableton_synth(
                request.descriptor,
                request.preset_name,
            )
        elif format_type == "ableton_drums":
            result = preset_export.export_ableton_drums(
                request.descriptor,
                request.machine_match,
                request.preset_name,
            )
        elif format_type == "ableton_live_set":
            if not request.full_result:
                raise HTTPException(
                    status_code=400,
                    detail="Full analysis result required for Live Set export",
                )
            result = preset_export.export_ableton_live_set(
                request.full_result,
                request.preset_name,
            )
        elif format_type == "project_bundle":
            # ZIP bundle with MIDI, stems, presets - works with ANY DAW
            if not request.full_result:
                raise HTTPException(
                    status_code=400,
                    detail="Full analysis result required for Project Bundle export",
                )
            result = preset_export.export_project_bundle(
                request.full_result,
                request.preset_name,
            )
        elif format_type == "text":
            if not request.full_result:
                raise HTTPException(
                    status_code=400,
                    detail="Full analysis result required for text export",
                )
            result = preset_export.export_text_analysis(
                request.full_result,
                request.preset_name,
            )
        elif format_type == "ableton_wavetable":
            # Get synth descriptor from full_result or descriptor
            synth_desc = request.descriptor
            if request.full_result and request.full_result.get("synth"):
                synth_desc = request.full_result["synth"].get("descriptor", synth_desc)
            if not synth_desc:
                raise HTTPException(
                    status_code=400,
                    detail="Synth analysis required for Wavetable export",
                )
            result = preset_export.export_ableton_wavetable(
                synth_desc,
                request.preset_name,
            )
        elif format_type == "ableton_analog":
            # Get synth descriptor from full_result or descriptor
            synth_desc = request.descriptor
            if request.full_result and request.full_result.get("synth"):
                synth_desc = request.full_result["synth"].get("descriptor", synth_desc)
            if not synth_desc:
                raise HTTPException(
                    status_code=400,
                    detail="Synth analysis required for Analog export",
                )
            result = preset_export.export_ableton_analog(
                synth_desc,
                request.preset_name,
            )
        elif format_type == "reconstruction":
            # End-to-end reconstruction: extracted MIDI + matched preset → .als
            # Phase 1 uses a fixed Analog preset; Phase 2 will swap in V2 retrieval.
            if not request.full_result:
                raise HTTPException(
                    status_code=400,
                    detail="Full analysis result required for reconstruction export",
                )
            # Carry midi_data forward if it was passed separately (frontend may
            # send it alongside full_result).
            full_result = dict(request.full_result)
            if request.midi_data and not full_result.get("midi_data"):
                full_result["midi_data"] = request.midi_data
            result = preset_export.export_reconstruction_als(
                full_result,
                request.preset_name,
            )
        elif format_type == "midi":
            # MIDI export uses stored midi_data from analysis
            if not request.midi_data:
                raise HTTPException(
                    status_code=400,
                    detail="MIDI data required. Re-analyze with MIDI extraction enabled.",
                )
            # Return the stored MIDI data
            from tone_forge.preset_export import ExportedPreset
            result = ExportedPreset(
                filename=request.midi_data.get("filename", f"{request.preset_name}.mid"),
                format="midi",
                content=request.midi_data.get("content", ""),
                content_type="audio/midi",
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown format: {format_type}. Supported: {list(preset_export.EXPORT_FORMATS.keys())}",
            )

        return JSONResponse({
            "filename": result.filename,
            "format": result.format,
            "content": result.content,
            "content_type": result.content_type,
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {e}") from e


@app.get("/api/export-formats")
async def list_export_formats() -> dict:
    """List available export formats."""
    from tone_forge import preset_export
    return {"formats": preset_export.EXPORT_FORMATS}


@app.post("/api/preview")
async def generate_tone_preview(request: Request) -> JSONResponse:
    """
    Generate a reconstruction preview of the detected tone.

    Uses IR convolution and simple amp simulation to create an audio
    preview of what the detected tone sounds like. Also returns
    matching reference tones from the library.

    Request body:
        descriptor: The tone descriptor from analysis
        midi_content: Optional base64 MIDI to render (otherwise uses test signal)
        preset_type: "guitar", "bass", or "synth"

    Returns:
        audio_b64: Base64 WAV of the preview
        reference: Matching reference tone info
        metadata: Processing details
    """
    from tone_forge import tone_preview

    try:
        body = await request.json()
        descriptor = body.get("descriptor")
        midi_content = body.get("midi_content")
        preset_type = body.get("preset_type", "guitar")

        # Validate descriptor
        if not descriptor:
            raise HTTPException(status_code=400, detail="No tone descriptor provided for preview")

        # Generate preview
        result = tone_preview.generate_preview_response(
            descriptor=descriptor,
            midi_content_b64=midi_content,
            preset_type=preset_type,
        )

        return JSONResponse(content=result)

    except Exception as e:
        logger.exception("Preview generation failed")
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {e}") from e


@app.get("/api/reference-tones")
async def get_reference_tones(
    amp_family: Optional[str] = None,
    gain_stage: Optional[str] = None,
) -> JSONResponse:
    """
    Get reference tone library entries.

    Can filter by amp_family and gain_stage.
    """
    from tone_forge.tone_preview import REFERENCE_LIBRARY

    tones = []
    for ref in REFERENCE_LIBRARY:
        if amp_family and ref.amp_family != amp_family:
            continue
        if gain_stage and ref.gain_stage != gain_stage:
            continue

        tones.append({
            "name": ref.name,
            "description": ref.description,
            "amp_family": ref.amp_family,
            "gain_stage": ref.gain_stage,
            "cab_type": ref.cab_type,
            "mic_type": ref.mic_type,
            "effects": ref.effects,
            "tags": ref.tags or [],
        })

    return JSONResponse(content={"tones": tones})


@app.post("/api/preset-match")
async def preset_match_endpoint(
    file: UploadFile = File(...),
    k: int = 5,
    instrument: str = "Analog",
    sound_type: Optional[str] = None,
) -> JSONResponse:
    """Match an uploaded audio clip against the rendered preset catalog.

    Returns the top-k closest presets by Euclidean distance over the
    8-feature catalog fingerprint (brightness/warmth/air/attack/decay/
    sustain/harmonic/pitch-stability).

    Query params:
        k: number of matches to return (default 5)
        instrument: catalog instrument to query (default Analog)
        sound_type: optional pre-filter (bass/lead/pad/keys/fx/percussion/other)
    """
    from tone_forge.preset_catalog.preset_retrieval import match_audio_file

    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix}. Accepted: {sorted(_ACCEPTED_SUFFIXES)}.",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        matches = match_audio_file(
            tmp_path,
            k=k,
            instrument=instrument,
            sound_type_filter=sound_type,
        )
        return JSONResponse(content={
            "filename": file.filename,
            "instrument": instrument,
            "sound_type_filter": sound_type,
            "k": k,
            "matches": matches,
        })
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


@app.get("/api/hardware-profiles")
async def get_hardware_profiles() -> JSONResponse:
    """Get available preset hardware profiles."""
    from tone_forge.hardware_profile import PRESET_PROFILES

    profiles = {}
    for key, profile in PRESET_PROFILES.items():
        profiles[key] = {
            "name": profile.name,
            "type": profile.primary_amp.type if profile.primary_amp else None,
            "max_blocks": profile.primary_amp.max_blocks if profile.primary_amp else None,
        }

    return JSONResponse(content={"profiles": profiles})


@app.get("/api/hardware-profile-template")
async def get_hardware_profile_template() -> JSONResponse:
    """Get a template for creating a custom hardware profile."""
    from tone_forge.hardware_profile import get_profile_template

    return JSONResponse(content={"template": get_profile_template()})


@app.get("/api/local-engine/download")
async def download_local_engine():
    """
    Download the local engine installer.

    Serves the actual DMG/installer file if available,
    otherwise shows an info page with instructions.
    """
    import platform

    system = platform.system()

    # Check for built installers
    dist_dir = Path(__file__).parent / "dist"
    installers = {
        "Darwin": dist_dir / "ToneForge-Studio.dmg",
        "Windows": dist_dir / "ToneForge Local Engine" / "ToneForge Local Engine.exe",
        "Linux": dist_dir / "toneforge-local",
    }

    installer_path = installers.get(system)

    # Serve the actual file if it exists
    if installer_path and installer_path.exists():
        filename = installer_path.name
        media_type = {
            ".dmg": "application/x-apple-diskimage",
            ".exe": "application/x-msdownload",
            "": "application/octet-stream",
        }.get(installer_path.suffix, "application/octet-stream")

        return FileResponse(
            path=str(installer_path),
            filename=filename,
            media_type=media_type,
        )

    # Fallback to info page
    platform_name = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(system, "macOS")

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>ToneForge Studio — Local Acceleration</title>
        <style>
            body {{
                font-family: 'IBM Plex Sans', system-ui, sans-serif;
                max-width: 540px;
                margin: 60px auto;
                padding: 24px;
                color: #1a1612;
                line-height: 1.6;
            }}
            h1 {{
                font-family: 'Fraunces', Georgia, serif;
                font-weight: 500;
                font-size: 28px;
                margin-bottom: 8px;
            }}
            .subtitle {{
                color: #666;
                font-size: 15px;
                margin-bottom: 32px;
            }}
            .benefit {{
                background: linear-gradient(135deg, #f8f5f0 0%, #f0ebe3 100%);
                border: 1px solid #e0d8cc;
                border-radius: 8px;
                padding: 24px;
                margin: 24px 0;
            }}
            .benefit h2 {{
                font-size: 14px;
                text-transform: uppercase;
                letter-spacing: 0.1em;
                color: #888;
                margin: 0 0 16px 0;
                font-weight: 500;
            }}
            .benefit ul {{
                margin: 0;
                padding-left: 20px;
            }}
            .benefit li {{
                margin: 10px 0;
                color: #444;
            }}
            .benefit strong {{
                color: #1a1612;
            }}
            .cta {{
                background: #1a1612;
                color: #fff;
                border: none;
                padding: 14px 28px;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 500;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
                margin: 8px 0;
            }}
            .cta:hover {{ background: #2d2520; }}
            .note {{
                font-size: 13px;
                color: #888;
                margin-top: 8px;
            }}
            .dev-section {{
                margin-top: 40px;
                padding-top: 24px;
                border-top: 1px solid #e0d8cc;
            }}
            .dev-section h3 {{
                font-size: 13px;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: #888;
                margin-bottom: 12px;
            }}
            code {{
                background: #ebe3d4;
                padding: 3px 8px;
                border-radius: 4px;
                font-family: 'IBM Plex Mono', monospace;
                font-size: 13px;
            }}
            .back {{
                display: inline-block;
                margin-top: 32px;
                color: #888;
                text-decoration: none;
                font-size: 14px;
            }}
            .back:hover {{ color: #1a1612; }}
        </style>
    </head>
    <body>
        <h1>ToneForge Studio</h1>
        <p class="subtitle">Local acceleration for professional workflows</p>

        <div class="benefit">
            <h2>What you get</h2>
            <ul>
                <li><strong>~2x faster</strong> stem separation and deep analysis</li>
                <li><strong>GPU acceleration</strong> using your machine's hardware</li>
                <li><strong>Private processing</strong> — audio stays on your computer</li>
                <li><strong>Automatic detection</strong> — works seamlessly with ToneForge</li>
            </ul>
        </div>

        <p class="note">
            Available for {platform_name}. Runs quietly in your menu bar.
        </p>

        <div class="dev-section">
            <h3>Developer Preview</h3>
            <p>Run from source while in development:</p>
            <p><code>python -m local_engine.tray</code></p>
        </div>

        <a href="/" class="back">&larr; Back to ToneForge</a>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@app.post("/api/local-engine/start")
async def start_local_engine():
    """
    Start the local GPU engine process.

    Spawns the local engine server as a background process.
    Returns immediately; use /health check on port 7777 to verify it started.
    """
    import subprocess
    import sys
    import signal

    # Check if already running and responsive
    try:
        import httpx
        resp = httpx.get("http://127.0.0.1:7777/health", timeout=2.0)
        if resp.status_code == 200:
            return JSONResponse({"status": "already_running", "message": "Local engine is already running"})
    except Exception:
        # Not responding - kill any hung processes before starting fresh
        try:
            result = subprocess.run(
                ["pkill", "-9", "-f", "local_engine/server.py"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                logger.info("Killed hung local engine process(es)")
                import time
                time.sleep(1)  # Wait for port to be released
        except Exception as e:
            logger.warning(f"Could not kill hung processes: {e}")

    # Start the local engine
    backend_dir = Path(__file__).parent
    server_script = backend_dir / "local_engine" / "server.py"

    if not server_script.exists():
        raise HTTPException(status_code=404, detail="Local engine server.py not found")

    try:
        # Use nohup to keep it running after this process ends
        process = subprocess.Popen(
            [sys.executable, str(server_script)],
            cwd=str(backend_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Detach from parent process
        )
        return JSONResponse({
            "status": "started",
            "message": "Local engine starting...",
            "pid": process.pid
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start local engine: {e}")


@app.post("/api/adapt-to-hardware")
async def adapt_to_hardware(request: Request) -> JSONResponse:
    """
    Adapt analysis output to a user's hardware profile.

    Request body:
        descriptor: Tone descriptor from analysis
        chain: Signal chain blocks
        profile: Hardware profile (from preset key or custom object)

    Returns:
        Adapted chain with suggestions and warnings.
    """
    from tone_forge import hardware_profile

    try:
        body = await request.json()
        descriptor = body.get("descriptor", {})
        chain = body.get("chain", [])
        profile_data = body.get("profile")

        # Load profile (preset key or custom)
        if isinstance(profile_data, str):
            # Preset profile key
            profile = hardware_profile.PRESET_PROFILES.get(profile_data)
            if not profile:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown profile: {profile_data}"
                )
        elif isinstance(profile_data, dict):
            # Custom profile
            profile = hardware_profile.HardwareProfile.from_dict(profile_data)
        else:
            raise HTTPException(
                status_code=400,
                detail="Profile must be a preset key or profile object"
            )

        # Adapt to profile
        result = hardware_profile.generate_profile_adapted_output(
            descriptor=descriptor,
            chain=chain,
            profile=profile,
        )

        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Hardware adaptation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/extract-midi")
async def extract_midi_endpoint(
    file: UploadFile = File(...),
    preset_name: str = "Extracted MIDI",
    min_note_duration_ms: float = 50,
    velocity_sensitivity: float = 1.0,
    quantize_to: Optional[int] = None,
    stem_type: str = "other",  # bass, drums, synth, pad, lead, vocals, other
) -> JSONResponse:
    """Extract MIDI notes from uploaded audio.

    Uses stem-specific extraction profiles for optimal results.

    Args:
        file: Audio file (WAV, MP3, etc.)
        preset_name: Name for the output MIDI file
        min_note_duration_ms: Minimum note duration in milliseconds
        velocity_sensitivity: Scale factor for velocity (1.0 = normal)
        quantize_to: If set, quantize to this note division (e.g., 16 = 16th notes)
        stem_type: Type of audio (bass, drums, synth, pad, lead, vocals, other)

    Returns:
        JSON with filename, content (base64), and extraction metadata
    """
    from tone_forge import midi_extractor

    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix}. Accepted: {sorted(_ACCEPTED_SUFFIXES)}.",
        )

    # Write to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        result = midi_extractor.extract_midi(
            str(tmp_path),
            preset_name=preset_name or file.filename or "Extracted MIDI",
            min_note_duration_ms=min_note_duration_ms,
            velocity_sensitivity=velocity_sensitivity,
            quantize_to=quantize_to,
            stem_type=stem_type,
        )

        return JSONResponse(_convert_numpy_types({
            "filename": result.filename,
            "content": result.content,
            "content_type": "audio/midi",
            "note_count": result.note_count,
            "duration_seconds": result.duration_seconds,
            "tempo_bpm": result.tempo_bpm,
            "pitch_range": {
                "lowest": result.pitch_range[0],
                "highest": result.pitch_range[1],
            },
        }))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MIDI extraction failed: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)


class RegionAnalysisRequest(BaseModel):
    """Request for region-focused analysis."""
    start_time: float
    end_time: float
    stem_type: str = "other"
    genre: Optional[str] = None
    track_id: Optional[str] = None
    include_midi: bool = True
    include_provenance: bool = True


@app.post("/api/analyze-region")
async def analyze_region_endpoint(
    file: UploadFile = File(...),
    start_time: float = Form(...),
    end_time: float = Form(...),
    stem_type: str = Form("other"),
    genre: Optional[str] = Form(None),
    track_id: Optional[str] = Form(None),
    include_midi: bool = Form(True),
    include_provenance: bool = Form(True),
) -> JSONResponse:
    """Analyze a specific time region of audio.

    Enables focused reconstruction analysis:
    - Re-analyze problematic sections
    - Extract MIDI for specific regions
    - Get detailed provenance per region
    - Loop section analysis

    Args:
        file: Audio file (WAV, MP3, etc.)
        start_time: Region start in seconds
        end_time: Region end in seconds
        stem_type: Type of stem (bass, drums, synth, pad, lead, vocals, other)
        genre: Genre hint for extraction
        track_id: Track identifier
        include_midi: Whether to extract MIDI
        include_provenance: Whether to include detailed provenance

    Returns:
        JSON with region analysis including notes, confidence, and provenance
    """
    from tone_forge.reconstruction.region_analyzer import RegionAnalyzer

    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix}. Accepted: {sorted(_ACCEPTED_SUFFIXES)}.",
        )

    # Write to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        import librosa

        # Load audio
        y, sr = librosa.load(str(tmp_path), sr=22050, mono=True)

        # Analyze region
        analyzer = RegionAnalyzer(sr=sr)
        result = analyzer.analyze_region(
            audio=y,
            sr=sr,
            start_time=start_time,
            end_time=end_time,
            stem_type=stem_type,
            genre=genre,
            track_id=track_id,
            include_midi=include_midi,
            include_provenance=include_provenance,
        )

        return JSONResponse(_convert_numpy_types(result.to_dict()))

    except Exception as e:
        logger.exception("Region analysis failed")
        raise HTTPException(status_code=500, detail=f"Region analysis failed: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/detect-sections")
async def detect_sections_endpoint(
    file: UploadFile = File(...),
    tempo: Optional[float] = Form(None),
    min_section_duration: float = Form(4.0),
) -> JSONResponse:
    """Detect arrangement sections in audio.

    Identifies structural sections like verse, chorus, drop, breakdown, etc.

    Args:
        file: Audio file (WAV, MP3, etc.)
        tempo: Optional tempo hint (auto-detected if not provided)
        min_section_duration: Minimum section duration in seconds

    Returns:
        JSON with detected sections, energy curve, and arrangement analysis
    """
    from tone_forge.analysis.sections import SectionDetector

    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix}. Accepted: {sorted(_ACCEPTED_SUFFIXES)}.",
        )

    # Write to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        import librosa

        # Load audio
        y, sr = librosa.load(str(tmp_path), sr=22050, mono=True)

        # Detect tempo if not provided
        if tempo is None:
            detected_tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            if hasattr(detected_tempo, "__iter__"):
                tempo = float(detected_tempo[0]) if len(detected_tempo) > 0 else 120.0
            else:
                tempo = float(detected_tempo) if detected_tempo > 0 else 120.0

        # Detect sections
        detector = SectionDetector(min_section_duration=min_section_duration)
        analysis = detector.detect_sections(audio=y, sr=sr, tempo=tempo)

        return JSONResponse(_convert_numpy_types(analysis.to_dict()))

    except Exception as e:
        logger.exception("Section detection failed")
        raise HTTPException(status_code=500, detail=f"Section detection failed: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/compare-regions")
async def compare_regions_endpoint(
    file: UploadFile = File(...),
    region_a_start: float = Form(...),
    region_a_end: float = Form(...),
    region_b_start: float = Form(...),
    region_b_end: float = Form(...),
    stem_type: str = Form("other"),
) -> JSONResponse:
    """Compare two regions for similarity.

    Useful for comparing verse 1 vs verse 2, or extraction variations.

    Args:
        file: Audio file (WAV, MP3, etc.)
        region_a_start: First region start in seconds
        region_a_end: First region end in seconds
        region_b_start: Second region start in seconds
        region_b_end: Second region end in seconds
        stem_type: Type of stem

    Returns:
        JSON with comparison metrics and similarity scores
    """
    from tone_forge.reconstruction.region_analyzer import RegionAnalyzer

    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix}. Accepted: {sorted(_ACCEPTED_SUFFIXES)}.",
        )

    # Write to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        import librosa

        # Load audio
        y, sr = librosa.load(str(tmp_path), sr=22050, mono=True)

        # Compare regions
        analyzer = RegionAnalyzer(sr=sr)
        result = analyzer.compare_regions(
            audio=y,
            sr=sr,
            region_a=(region_a_start, region_a_end),
            region_b=(region_b_start, region_b_end),
            stem_type=stem_type,
        )

        return JSONResponse(_convert_numpy_types(result))

    except Exception as e:
        logger.exception("Region comparison failed")
        raise HTTPException(status_code=500, detail=f"Region comparison failed: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/api/synth-hardware")
async def list_synth_hardware() -> dict:
    """List available hardware synths for translation."""
    from tone_forge import synth_hardware
    return {"hardware": synth_hardware.get_available_hardware()}


class SynthHardwareRequest(BaseModel):
    synth_descriptor: dict
    hardware_id: str


@app.post("/api/synth-hardware")
async def translate_synth_hardware(request: SynthHardwareRequest) -> JSONResponse:
    """Translate synth descriptor to hardware synth settings.

    Returns knob positions and tips for recreating the sound
    on a specific hardware synthesizer.
    """
    from tone_forge import synth_hardware
    from tone_forge.synth_analyzer import SynthDescriptor, SynthOscillator, SynthFilter, SynthEnvelope, SynthLFO

    # Reconstruct the descriptor from dict
    desc_dict = request.synth_descriptor
    try:
        osc = SynthOscillator(
            type=desc_dict.get("oscillator", {}).get("type", "saw"),
            num_voices=desc_dict.get("oscillator", {}).get("num_voices", 1),
            detune=desc_dict.get("oscillator", {}).get("detune", 0),
            sub_osc=desc_dict.get("oscillator", {}).get("sub_osc", False),
        )
        filt = SynthFilter(
            cutoff_hz=desc_dict.get("filter", {}).get("cutoff_hz", 8000),
            cutoff_normalized=desc_dict.get("filter", {}).get("cutoff_normalized", 0.8),
            resonance=desc_dict.get("filter", {}).get("resonance", 0),
        )
        env = SynthEnvelope(
            attack_ms=desc_dict.get("amp_envelope", {}).get("attack_ms", 10),
            decay_ms=desc_dict.get("amp_envelope", {}).get("decay_ms", 100),
            sustain=desc_dict.get("amp_envelope", {}).get("sustain", 0.7),
            release_ms=desc_dict.get("amp_envelope", {}).get("release_ms", 200),
        )
        lfo = None
        if desc_dict.get("lfo"):
            lfo = SynthLFO(
                rate_hz=desc_dict["lfo"].get("rate_hz", 0),
                depth=desc_dict["lfo"].get("depth", 0),
                target=desc_dict["lfo"].get("target", "filter"),
            )

        descriptor = SynthDescriptor(
            oscillator=osc,
            filter=filt,
            amp_envelope=env,
            lfo=lfo,
            has_chorus=desc_dict.get("has_chorus", False),
            has_phaser=desc_dict.get("has_phaser", False),
            has_reverb=desc_dict.get("has_reverb", False),
            has_delay=desc_dict.get("has_delay", False),
            brightness=desc_dict.get("brightness", 0.5),
            duration_sec=desc_dict.get("duration_sec", 0),
        )

        config = synth_hardware.translate_to_hardware(descriptor, request.hardware_id)

        if not config:
            raise HTTPException(status_code=400, detail=f"Unknown hardware: {request.hardware_id}")

        return JSONResponse({
            "synth_name": config.synth_name,
            "synth_model": config.synth_model,
            "description": config.description,
            "controls": [
                {
                    "name": c.name,
                    "value": c.value,
                    "display": c.display,
                    "note": c.note,
                }
                for c in config.controls
            ],
            "notes": config.notes,
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {e}") from e


def _parse_youtube_timestamp(url: str) -> int:
    """Parse timestamp from YouTube URL.

    Supports formats:
    - ?t=120 or &t=120 (seconds)
    - ?t=2m30s or &t=2m30s (minutes and seconds)
    - ?t=1h2m30s (hours, minutes, seconds)

    Returns timestamp in seconds, or 0 if no timestamp found.
    """
    import re
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    # Get the 't' parameter
    t_param = params.get('t', [None])[0]
    if not t_param:
        return 0

    # Try parsing as plain seconds
    if t_param.isdigit():
        return int(t_param)

    # Try parsing format like "2m30s" or "1h2m30s"
    total_seconds = 0
    hours = re.search(r'(\d+)h', t_param)
    minutes = re.search(r'(\d+)m', t_param)
    seconds = re.search(r'(\d+)s', t_param)

    if hours:
        total_seconds += int(hours.group(1)) * 3600
    if minutes:
        total_seconds += int(minutes.group(1)) * 60
    if seconds:
        total_seconds += int(seconds.group(1))

    return total_seconds


def _format_timestamp(seconds: int) -> str:
    """Format seconds as MM:SS or HH:MM:SS for yt-dlp."""
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    else:
        m = seconds // 60
        s = seconds % 60
        return f"{m}:{s:02d}"


# YouTube audio cache directory
YOUTUBE_CACHE_DIR = Path(tempfile.gettempdir()) / "toneforge_yt_cache"
YOUTUBE_CACHE_DIR.mkdir(exist_ok=True)
CACHE_MAX_AGE_HOURS = 24  # Delete cached files older than this


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    from urllib.parse import urlparse, parse_qs
    import re

    parsed = urlparse(url)

    # Handle youtu.be short URLs
    if parsed.netloc in ('youtu.be', 'www.youtu.be'):
        return parsed.path.lstrip('/')

    # Handle youtube.com URLs
    if 'youtube' in parsed.netloc:
        # Standard watch URL
        if parsed.path == '/watch':
            params = parse_qs(parsed.query)
            return params.get('v', [None])[0]
        # Embed or shorts URL
        match = re.match(r'/(embed|shorts|v)/([^/?]+)', parsed.path)
        if match:
            return match.group(2)

    return None


def _get_cache_key(video_id: str, start_time: int, duration: int) -> str:
    """Generate cache key for a specific video segment."""
    return f"{video_id}_{start_time}_{duration}"


def _get_cached_audio(video_id: str, start_time: int, duration: int) -> tuple[Path, str] | None:
    """Check if audio is cached and return (path, title) if exists."""
    cache_key = _get_cache_key(video_id, start_time, duration)
    cache_path = YOUTUBE_CACHE_DIR / f"{cache_key}.wav"
    meta_path = YOUTUBE_CACHE_DIR / f"{cache_key}.json"

    if cache_path.exists():
        # Check if cache is not too old
        age_hours = (datetime.now().timestamp() - cache_path.stat().st_mtime) / 3600
        if age_hours < CACHE_MAX_AGE_HOURS:
            # Try to load title from metadata
            title = cache_key  # Default to cache key
            if meta_path.exists():
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                        title = meta.get("title", cache_key)
                except Exception:
                    pass
            logger.info(f"Using cached audio: {title}")
            return cache_path, title
        else:
            # Cache expired, delete it
            cache_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

    return None


def _cache_audio(source_path: Path, video_id: str, start_time: int, duration: int, title: str = None) -> Path:
    """Copy audio file to cache with metadata and return cache path."""
    cache_key = _get_cache_key(video_id, start_time, duration)
    cache_path = YOUTUBE_CACHE_DIR / f"{cache_key}.wav"
    meta_path = YOUTUBE_CACHE_DIR / f"{cache_key}.json"

    shutil.copy2(source_path, cache_path)

    # Save metadata including title
    if title:
        with open(meta_path, 'w') as f:
            json.dump({"title": title, "video_id": video_id, "start_time": start_time}, f)

    logger.info(f"Cached audio: {title or cache_path.name}")
    return cache_path


def _cleanup_old_cache():
    """Remove cache files older than CACHE_MAX_AGE_HOURS."""
    try:
        now = datetime.now().timestamp()
        for f in YOUTUBE_CACHE_DIR.glob("*.wav"):
            age_hours = (now - f.stat().st_mtime) / 3600
            if age_hours > CACHE_MAX_AGE_HOURS:
                f.unlink(missing_ok=True)
                logger.debug(f"Removed old cache: {f.name}")
    except Exception as e:
        logger.warning(f"Cache cleanup error: {e}")


def _download_youtube_audio(url: str, output_dir: Path, duration: int = 30) -> tuple[Path, int, str]:
    """Download audio from a YouTube URL using yt-dlp.

    Args:
        url: YouTube URL (can include timestamp like ?t=120)
        output_dir: Directory to save the file
        duration: Seconds to download (default 30)

    Returns tuple of (path to downloaded file, start timestamp in seconds, display name).
    """
    import sys

    # Parse timestamp from URL
    start_time = _parse_youtube_timestamp(url)

    # Check cache first
    video_id = _extract_video_id(url)
    if video_id:
        cached_result = _get_cached_audio(video_id, start_time, duration)
        if cached_result:
            cached_path, cached_title = cached_result
            # Copy cached file to output_dir for consistency
            output_path = output_dir / f"{cached_title}.wav"
            shutil.copy2(cached_path, output_path)
            return output_path, start_time, cached_title

        # Cleanup old cache files occasionally
        _cleanup_old_cache()

    output_template = str(output_dir / "%(title).50s.%(ext)s")
    end_time = start_time + duration

    # Format times for yt-dlp
    start_str = _format_timestamp(start_time)
    end_str = _format_timestamp(end_time)

    logger.info(f"Downloading {duration}s starting at {start_str} (t={start_time}s)")

    # Use python -m yt_dlp to ensure we find the installed package
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--extract-audio",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "--no-playlist",
        # Download specific section based on timestamp
        "--download-sections", f"*{start_str}-{end_str}",
        "--output", output_template,
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    if result.returncode != 0:
        # Fallback: some videos don't support sections, try without limit
        logger.info("Section download failed, trying full download...")
        cmd_fallback = [
            sys.executable, "-m", "yt_dlp",
            "--extract-audio",
            "--audio-format", "wav",
            "--audio-quality", "0",
            "--no-playlist",
            "--output", output_template,
            url,
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {result.stderr}")

    # Find the downloaded file
    wav_files = list(output_dir.glob("*.wav"))
    if not wav_files:
        raise RuntimeError("No audio file was downloaded")

    downloaded_file = wav_files[0]
    # Extract title from filename (yt-dlp uses video title)
    display_name = downloaded_file.stem

    # Cache the downloaded file for future use
    if video_id:
        _cache_audio(downloaded_file, video_id, start_time, duration, title=display_name)

    return downloaded_file, start_time, display_name


@app.post("/api/analyze-url")
async def analyze_url_endpoint(request: UrlAnalyzeRequest) -> JSONResponse:
    """Analyze audio from a YouTube URL using unified pipeline.

    Downloads the audio using yt-dlp, then runs analysis.
    Supports multiple platforms: helix, pedals, synth.
    """
    if not _check_yt_dlp():
        raise HTTPException(
            status_code=501,
            detail="yt-dlp is not installed. Install with: pip install yt-dlp",
        )

    url = request.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL format.")

    # Build unified pipeline config
    if request.fast_mode:
        config = PipelineConfig.fast()
    elif getattr(request, 'analysis_mode', 'studio').lower() == "deep":
        config = PipelineConfig.deep()
    else:
        config = PipelineConfig.standard()

    # Apply options from request
    config.extract_midi = request.extract_midi
    config.trim_start = request.start_time
    config.trim_end = request.end_time
    config.source_url = url

    # Create temp directory for download
    tmp_dir = Path(tempfile.mkdtemp(prefix="toneforge_yt_"))

    try:
        # Download audio
        logger.info(f"Downloading audio from: {url} (mode={config.mode.value})")
        audio_path, start_timestamp, display_name = _download_youtube_audio(
            url, tmp_dir, duration=_MAX_PREVIEW_DURATION
        )
        config.source_name = display_name

        # Use unified pipeline
        pipeline = get_unified_pipeline()
        result = await pipeline.analyze(audio_path, config)

        # Convert to response dict
        response = result.to_dict()
        response["source_url"] = url
        response["source_timestamp"] = start_timestamp if start_timestamp > 0 else None
        response["analysis_mode"] = config.mode.value

        # Add to history
        history_entry = _add_to_history({
            "name": display_name or url[:50],
            "detected_type": response.get("detected_type", "guitar"),
            "summary": response.get("detection", {}).get("summary", ""),
            "duration": response.get("duration_sec"),
            "source_url": url,
        }, full_result=response)

        response["history_id"] = history_entry["id"]

        return JSONResponse(_convert_numpy_types(response))

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Download timed out. Try a shorter video.")
    except Exception as e:
        logger.exception("URL analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}") from e
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/api/analyze-url-stream")
async def analyze_url_stream_endpoint(request: UrlAnalyzeRequest):
    """Analyze audio from a YouTube URL using unified pipeline with SSE progress.

    Returns Server-Sent Events with progress updates, then final result.
    Deep analysis REQUIRES local engine to avoid blocking the server.
    """
    import httpx

    analysis_mode = getattr(request, 'analysis_mode', 'quick')
    is_deep = analysis_mode.lower() == "deep" and not request.fast_mode

    # Debug: Log the incoming request parameters
    logger.info(f"[analyze-url-stream] use_local_engine={request.use_local_engine}, analysis_mode={analysis_mode}, fast_mode={request.fast_mode}, is_deep={is_deep}")

    # Deep analysis requires local engine to prevent server blocking
    if is_deep and not request.use_local_engine:
        return JSONResponse(
            status_code=400,
            content={
                "detail": "Deep analysis requires local GPU engine. Please start the local engine or select Quick/Standard mode.",
                "error_code": "LOCAL_ENGINE_REQUIRED"
            }
        )

    # Build unified pipeline config
    if request.fast_mode:
        config = PipelineConfig.fast()
    elif getattr(request, 'analysis_mode', 'studio').lower() == "deep":
        config = PipelineConfig.deep()
    else:
        config = PipelineConfig.standard()

    # Apply options from request
    config.extract_midi = request.extract_midi
    config.trim_start = request.start_time
    config.trim_end = request.end_time
    config.source_url = request.url

    async def generate():
        def send_event(event_type: str, data: dict):
            return f"data: {json.dumps({'type': event_type, **data})}\n\n"

        if not _check_yt_dlp():
            yield send_event("error", {"message": "yt-dlp not installed"})
            return

        url = request.url.strip()
        if not url.startswith(("http://", "https://")):
            yield send_event("error", {"message": "Invalid URL format"})
            return

        tmp_dir = Path(tempfile.mkdtemp(prefix="toneforge_yt_"))

        try:
            # Download audio first (outside pipeline for better progress reporting)
            yield send_event("progress", {"message": "Downloading audio...", "percent": 5, "stage": "download"})
            await asyncio.sleep(0)

            audio_path, start_timestamp, display_name = _download_youtube_audio(
                url, tmp_dir, duration=_MAX_PREVIEW_DURATION
            )
            config.source_name = display_name

            # If local engine requested, proxy the downloaded audio there
            if request.use_local_engine:
                yield send_event("progress", {"message": "Sending to local GPU engine...", "percent": 10, "stage": "proxy"})
                await asyncio.sleep(0)

                LOCAL_ENGINE_URL = "http://127.0.0.1:7777"

                try:
                    import requests
                    from queue import Queue
                    import threading

                    # Queue for streaming events from background thread
                    event_queue: Queue = Queue()

                    async def proxy_to_local_engine_async():
                        """Async proxy using httpx for proper SSE streaming."""
                        try:
                            logger.info(f"[proxy] Uploading {audio_path.name} to local engine...")
                            event_queue.put(("data", json.dumps({"type": "progress", "stage": "upload", "message": "Uploading to GPU...", "progress": 0.12})))

                            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
                                with open(audio_path, "rb") as f:
                                    files = {"file": (audio_path.name, f, "audio/wav")}
                                    async with client.stream("POST", f"{LOCAL_ENGINE_URL}/api/analyze-deep", files=files) as resp:
                                        logger.info(f"[proxy] Connected, status={resp.status_code}")
                                        event_queue.put(("data", json.dumps({"type": "progress", "stage": "processing", "message": "GPU processing...", "progress": 0.15})))

                                        if resp.status_code != 200:
                                            event_queue.put(("error", f"Local engine error: {resp.status_code}"))
                                            return

                                        # Stream lines as they arrive
                                        async for line in resp.aiter_lines():
                                            if line.startswith("data: "):
                                                event_queue.put(("data", line[6:]))
                                                logger.debug(f"[proxy] Got event: {line[:60]}...")

                            event_queue.put(("done", None))
                            logger.info("[proxy] Local engine stream completed")
                        except Exception as e:
                            logger.error(f"[proxy] Error: {e}")
                            event_queue.put(("error", str(e)))

                    def proxy_to_local_engine():
                        """Run async proxy in new event loop."""
                        import asyncio
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(proxy_to_local_engine_async())
                        finally:
                            loop.close()

                    # Start background thread
                    thread = threading.Thread(target=proxy_to_local_engine)
                    thread.start()

                    # Process events from queue
                    while True:
                        # Non-blocking check with small timeout
                        await asyncio.sleep(0.1)
                        while not event_queue.empty():
                            event_type, event_data = event_queue.get_nowait()

                            if event_type == "error":
                                yield send_event("error", {"message": event_data})
                                thread.join()
                                return

                            if event_type == "done":
                                thread.join()
                                return

                            if event_type == "data":
                                try:
                                    local_data = json.loads(event_data)
                                    event_type_local = local_data.get("type")

                                    # Forward progress events
                                    if event_type_local == "progress" or local_data.get("stage"):
                                        progress = local_data.get("progress", 0)
                                        if isinstance(progress, float) and progress <= 1:
                                            progress = int(progress * 100)
                                        adjusted = 10 + int(progress * 0.85)
                                        yield send_event("progress", {
                                            "message": local_data.get("message", "Processing..."),
                                            "percent": adjusted,
                                            "stage": local_data.get("stage", "local"),
                                        })
                                    # Forward final result - local engine sends {"type": "result", "data": {...}}
                                    elif event_type_local == "result":
                                        result_data = local_data.get("data", local_data)
                                        # Add source URL to result
                                        result_data["source_url"] = url
                                        result_data["source_name"] = display_name or url[:50]
                                        # P2k: persist to history so the Jam UI can
                                        # write history_id into the URL bar — same
                                        # contract as the server-pipeline branch
                                        # below. Without this the local-engine path
                                        # produces no id, the URL stays bare /jam,
                                        # and a refresh drops the song.
                                        try:
                                            history_entry = _add_to_history({
                                                "name": display_name or url[:50],
                                                "detected_type": result_data.get("detected_type", "guitar"),
                                                "summary": result_data.get("detection", {}).get("summary", ""),
                                                "duration": result_data.get("duration_sec"),
                                                "source_url": url,
                                                "deep_analysis": is_deep,
                                            }, full_result=result_data)
                                            result_data["history_id"] = history_entry["id"]
                                        except Exception as hist_err:
                                            logger.warning(f"[analyze-url-stream] history persist failed on local-engine path: {hist_err}")
                                        yield send_event("progress", {"message": "Complete!", "percent": 100})
                                        yield send_event("result", {"data": _convert_numpy_types(result_data)})
                                    # Forward early-stems event so the frontend
                                    # can start decoding audio while MIDI /
                                    # analysis are still running upstream.
                                    elif event_type_local == "stems_partial":
                                        yield send_event("stems_partial", {
                                            "stem_records": local_data.get("stem_records", []),
                                            "stems": local_data.get("stems", {}),
                                        })
                                    # Handle error events
                                    elif event_type_local == "error":
                                        yield send_event("error", {"message": local_data.get("message", "Unknown error")})
                                except json.JSONDecodeError:
                                    pass

                        if not thread.is_alive() and event_queue.empty():
                            break

                    return

                except Exception as e:
                    logger.error(f"Local engine proxy error: {e}", exc_info=True)
                    yield send_event("error", {"message": f"Local engine error: {e}"})
                    return

            # Use unified pipeline (server-side processing)
            pipeline = get_unified_pipeline()

            async for event in pipeline.analyze_streaming(audio_path, config):
                if isinstance(event, ProgressEvent):
                    # Adjust progress (download took 5%, pipeline takes 5-95%)
                    adjusted_percent = 5 + int(event.percent * 0.9)
                    yield send_event("progress", {
                        "message": event.message,
                        "percent": adjusted_percent,
                        "stage": event.stage,
                    })
                    await asyncio.sleep(0)
                elif isinstance(event, AnalysisResult):
                    # Convert to response dict
                    response = event.to_dict()
                    response["source_url"] = url
                    response["source_timestamp"] = start_timestamp if start_timestamp > 0 else None
                    response["analysis_mode"] = config.mode.value
                    response["deep_analysis"] = config.mode == UnifiedAnalysisMode.DEEP

                    # Guitar tone recommendation — mirrors the worker-side
                    # injection in local_engine/analysis_worker.py so both
                    # backends emit the same wire shape. Failures here
                    # never break analysis.
                    try:
                        from tone_forge.tone import guitar_catalog as _gc
                        from tone_forge.tone import instrumentation as _tone_log

                        _stems_map = response.get("stems_paths") or response.get("stems") or {}
                        _raw_stem_path = (
                            _stems_map.get("other")
                            or _stems_map.get("guitar")
                            or audio_path
                        )
                        # Unwrap a "<url-base>?path=<abspath>" stem URL.
                        if isinstance(_raw_stem_path, str) and "?path=" in _raw_stem_path:
                            _raw_stem_path = _raw_stem_path.split("?path=", 1)[1]
                        _tone_rec = _gc.recommend_from_tempo_key(
                            Path(_raw_stem_path) if _raw_stem_path else None,
                            tempo_bpm=response.get("tempo_bpm"),
                            key=response.get("detected_key"),
                        )
                        response["tone"] = _gc.to_wire_dict(_tone_rec)
                        _tone_log.log_recommendation(_tone_rec, source_url=url)
                    except Exception as _tone_exc:
                        logger.warning(f"[analyze-url-stream] tone recommendation failed (server path): {_tone_exc}")
                        response["tone"] = None

                    # Add to history
                    history_entry = _add_to_history({
                        "name": display_name or url[:50],
                        "detected_type": response.get("detected_type", "guitar"),
                        "summary": response.get("detection", {}).get("summary", ""),
                        "duration": response.get("duration_sec"),
                        "source_url": url,
                        "deep_analysis": config.mode == UnifiedAnalysisMode.DEEP,
                    }, full_result=response)

                    response["history_id"] = history_entry["id"]

                    if config.mode == UnifiedAnalysisMode.DEEP:
                        response["admin_url"] = f"/studio?analysis={history_entry['id']}"

                    yield send_event("progress", {"message": "Complete!", "percent": 100})
                    yield send_event("result", {"data": _convert_numpy_types(response)})

        except Exception as e:
            logger.error(f"analyze-url-stream error: {e}", exc_info=True)
            yield send_event("error", {"message": str(e)})
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# =============================================================================
# Feedback API - Collect user corrections for ML training
# =============================================================================

class BlockFeedbackRequest(BaseModel):
    """User feedback on block recommendations."""
    analysis_id: str
    slot: str  # "amp", "cab", "drive", etc.
    selected_block_id: str
    selected_block_family: str
    was_top_pick: bool = False
    original_rank: int = 0
    rating: Optional[float] = None  # 1-5 rating
    notes: Optional[str] = None


@app.post("/api/feedback/block")
async def submit_block_feedback(feedback: BlockFeedbackRequest):
    """Submit feedback on a block recommendation."""
    try:
        from tone_forge.ml.translator import submit_feedback

        submit_feedback(
            analysis_id=feedback.analysis_id,
            slot=feedback.slot,
            selected_block_id=feedback.selected_block_id,
            selected_block_family=feedback.selected_block_family,
            was_top_pick=feedback.was_top_pick,
            original_rank=feedback.original_rank,
            rating=feedback.rating,
            notes=feedback.notes,
        )

        return {"success": True}
    except ImportError:
        # ML module not available - silently accept feedback
        return {"success": True, "note": "ML not available"}
    except Exception as e:
        logger.exception("Failed to submit block feedback")
        raise HTTPException(status_code=500, detail=str(e))


class ParameterFeedbackRequest(BaseModel):
    """User feedback on parameter adjustments."""
    analysis_id: str
    slot: str
    block_id: str
    parameter: str
    original_value: float
    adjusted_value: float


@app.post("/api/feedback/parameter")
async def submit_parameter_feedback(feedback: ParameterFeedbackRequest):
    """Submit feedback on a parameter adjustment."""
    try:
        from tone_forge.ml.preferences import get_tracker

        tracker = get_tracker()
        tracker.track_parameter_edit(
            slot=feedback.slot,
            block_id=feedback.block_id,
            parameter=feedback.parameter,
            old_value=feedback.original_value,
            new_value=feedback.adjusted_value,
        )

        return {"success": True}
    except ImportError:
        return {"success": True, "note": "ML not available"}
    except Exception as e:
        logger.exception("Failed to submit parameter feedback")
        raise HTTPException(status_code=500, detail=str(e))


class ExportFeedbackRequest(BaseModel):
    """Feedback when user exports a preset."""
    analysis_id: str
    export_format: str  # "helix", "pedals", etc.
    rating: Optional[float] = None


@app.post("/api/feedback/export")
async def submit_export_feedback(feedback: ExportFeedbackRequest):
    """Submit feedback when exporting (indicates success)."""
    try:
        from tone_forge.ml.preferences import get_tracker
        from tone_forge.ml.retrieval import get_augmenter

        # Track export
        tracker = get_tracker()
        tracker.track_export(
            analysis_id=feedback.analysis_id,
            export_format=feedback.export_format,
            rating=feedback.rating,
        )

        # Mark reference as successful export
        augmenter = get_augmenter()
        augmenter.mark_successful(
            feedback.analysis_id,
            rating=feedback.rating,
        )

        return {"success": True}
    except ImportError:
        return {"success": True, "note": "ML not available"}
    except Exception as e:
        logger.exception("Failed to submit export feedback")
        raise HTTPException(status_code=500, detail=str(e))


class AnalysisFeedbackRequest(BaseModel):
    """General feedback on analysis quality."""
    analysis_id: str
    overall_rating: float  # 1-5
    descriptor_accuracy: Optional[float] = None
    recommendations_usefulness: Optional[float] = None
    notes: Optional[str] = None


@app.post("/api/feedback/analysis")
async def submit_analysis_feedback(feedback: AnalysisFeedbackRequest):
    """Submit overall feedback on an analysis."""
    try:
        from tone_forge.ml.preferences import get_tracker

        tracker = get_tracker()
        tracker.track_analysis_feedback(
            analysis_id=feedback.analysis_id,
            overall_rating=feedback.overall_rating,
            descriptor_accuracy=feedback.descriptor_accuracy,
            recommendations_usefulness=feedback.recommendations_usefulness,
            notes=feedback.notes,
        )

        return {"success": True}
    except ImportError:
        return {"success": True, "note": "ML not available"}
    except Exception as e:
        logger.exception("Failed to submit analysis feedback")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/feedback/stats")
async def get_feedback_stats():
    """Get feedback statistics (for admin/debugging)."""
    try:
        from tone_forge.ml.translator import get_feedback_stats
        from tone_forge.ml.preferences import get_tracker

        tracker = get_tracker()

        return {
            "translator_feedback": get_feedback_stats(),
            "behavior_stats": tracker.get_aggregated_stats(),
        }
    except ImportError:
        return {"error": "ML not available"}
    except Exception as e:
        logger.exception("Failed to get feedback stats")
        raise HTTPException(status_code=500, detail=str(e))
