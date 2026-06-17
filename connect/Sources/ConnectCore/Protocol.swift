//
// Protocol.swift — wire-protocol constants for the Connect bridge.
//
// Owning this on the Swift side mirrors the server-side constant in
// tone_forge_api.py (CONNECT_BRIDGE_PROTOCOL_VERSION). Both sides
// bump together — that is the entire point of versioning.
//
// Per ONBOARDING_AUDIT §F4.3: when the server requires a newer
// version than this build supports, Connect surfaces "ToneForge has
// been updated. Please update Connect." with a Check-for-Updates
// affordance, instead of silently misbehaving.
//

import Foundation

public enum ConnectProtocol {

    /// Wire-protocol version this build of Connect speaks.
    /// Increment when adding required fields or changing semantics
    /// of any existing message type. Optional additive fields do not
    /// require a version bump.
    ///
    /// v2 (Audio-Ownership Pivot): additive over v1. New message
    /// types only — `session_data`, `transport_state`, `connect_state`,
    /// `latency_report`, `input_meter`. No existing v1 frame shape
    /// changed; a v1 helper talking to a v2 server still receives
    /// every v1 frame unchanged. Server's hello_ack negotiates the
    /// effective version down to min(client, server).
    public static let version: Int = 2

    /// Message type strings. Defined here (not as a Swift enum) so
    /// unknown types from a future server land in the default branch
    /// of the dispatcher rather than crashing JSON decode.
    public enum MessageType {
        public static let hello             = "hello"
        public static let helloAck          = "hello_ack"
        public static let versionMismatch   = "version_mismatch"
        public static let joined            = "joined"
        public static let presetPush        = "preset_push"
        public static let setGain           = "set_gain"
        /// Monitor chain applied at the API edge. Frame shape:
        ///   { "type": "apply_chain", "chain_id": "...", "chain": {...} }
        /// The server resolves chain_id → spec from the bundled bank
        /// (backend/tone_forge/monitor/chains/*.yaml) so Connect does
        /// not ship a YAML parser. Spec round-trip: load_chain →
        /// _monitor_chain_to_wire → ChainSpec.decode(fromWireDict:).
        public static let applyChain        = "apply_chain"
        public static let ping              = "ping"
        public static let pong              = "pong"
        public static let ack               = "ack"
        public static let error             = "error"
        /// Connect → server event: the helper's audio engine could not
        /// recover from a device change (e.g. interface unplugged,
        /// default-device swap that left the engine in `.failed`).
        /// Emitted exactly once when `AudioEngine.attemptReconfigRestart`
        /// exhausts its retry budget; broadcast by the server to any
        /// browser peer in the channel, which surfaces a user-facing
        /// reconnection toast.
        public static let deviceLost        = "device_lost"
        /// Server → Connect notification: the user toggled Sparkle's
        /// auto-update preference in the browser. Frame shape:
        ///   { "v": 1, "type": "set_auto_update",
        ///     "enabled": true|false, "replayed": true|false }
        /// On receipt, Connect writes the value to UserDefaults under
        /// the `SUEnableAutomaticChecks` key so Sparkle picks it up
        /// on its next scheduled-check tick. `replayed: true` means
        /// the value arrived via the join-time replay path (i.e. on
        /// a fresh helper bring-up); `replayed: false` means a live
        /// user-initiated change. Connect treats them identically.
        public static let setAutoUpdate     = "set_auto_update"

        // ----- v2 additions (Audio-Ownership Pivot) -----
        //
        // These types carry the JAM↔Connect handoff that turns
        // Connect into the authoritative audio engine and JAM into
        // the musician interface. All are additive: a v1 build of
        // either side simply lands them in its default dispatch
        // branch and ignores them.

        /// Browser → Connect. Song-level metadata pushed after JAM
        /// finishes analysis. Connect today routes this to a no-op
        /// callback in PresetBridge; future consumer is an in-Connect
        /// SessionStore for tuner / countdown / chord HUD overlays.
        /// Frame shape (all fields below `type`/`v` nullable):
        ///   { "type": "session_data", "v": 2,
        ///     "session_id": "...", "song": {...},
        ///     "bpm": 120.0, "key": {"root": 0, "scale": "Major"},
        ///     "chord_progression": [{"symbol","start_s","end_s"}],
        ///     "section_markers":   [{"name","start_s","end_s"}],
        ///     "loop_markers":      {"start_s","end_s"} }
        public static let sessionData       = "session_data"

        /// Browser → Connect. Playback transport tick. NOT cached
        /// server-side (high-rate, stale-on-arrival).
        ///   { "type": "transport_state", "v": 2,
        ///     "playing": true, "position_s": 12.34 }
        public static let transportState    = "transport_state"

        /// Connect → Browser. Engine snapshot. Rate-limited to ~1 Hz
        /// in Connect; emitted on every state transition and on
        /// monitor-gain/mute changes. Cached server-side so a
        /// late-joining JAM sees the latest on join.
        ///   { "type": "connect_state", "v": 2,
        ///     "state": "stopped|starting|running|reconfiguring|failed",
        ///     "device": {"input_name","output_name","sample_rate","channels_in"},
        ///     "monitor": {"enabled","gain","muted"},
        ///     "dsp": {"amp_sim_enabled","active_chain_id"} }
        public static let connectState      = "connect_state"

        /// Connect → Browser. Measured engine latency. Emitted when
        /// engine reaches .running and on device/sample-rate change.
        /// NOT per-frame. Cached server-side.
        ///   { "type": "latency_report", "v": 2,
        ///     "input_ms": 3.1, "output_ms": 4.2,
        ///     "buffer_ms": 5.3,
        ///     "estimated_round_trip_ms": 17.9,
        ///     // Optional, only present after a successful LatencyProbe
        ///     // impulse-loopback run. Estimated_* fields are the floor
        ///     // derived from CoreAudio AU properties; measured_* is the
        ///     // ground-truth round-trip from playing a 1 kHz tone and
        ///     // listening for it on the input bus.
        ///     "measured_round_trip_ms": 12.4,
        ///     "measurement_confidence": "high" }   // high|low|no_signal
        public static let latencyReport     = "latency_report"

        /// Browser → Connect. Trigger an impulse-loopback LatencyProbe
        /// run inside the helper. Connect plays a brief 1 kHz tone and
        /// listens for it on the input bus; result lands in the next
        /// `latency_report` frame's `measured_round_trip_ms`. NOT
        /// cached server-side — the user must request it explicitly
        /// because the impulse is audible.
        ///   { "type": "measure_latency", "v": 2 }
        public static let measureLatency    = "measure_latency"

        /// Connect → Browser. Input level for the JAM-side VU meter.
        /// Rate-limited to ~20 Hz at the source. NOT cached
        /// server-side.
        ///   { "type": "input_meter", "v": 2,
        ///     "peak_dbfs": -12.4, "rms_dbfs": -18.7 }
        public static let inputMeter        = "input_meter"

        /// Browser → Connect. Hand off a song's stem URLs so Connect
        /// can take over playback. Each entry is a stem id JAM uses
        /// internally (e.g. "demucs.drums") plus the audio_url the
        /// backend's `/api/serve-file` produced. Connect downloads
        /// each URL to a per-session cache dir, then attaches them
        /// via `AudioEngine.loadStem`. Cached server-side so a
        /// late-joining Connect (e.g. user relaunched the helper
        /// mid-song) sees the current stem set on join.
        ///   { "type": "load_stems", "v": 2,
        ///     "stems": [
        ///       {"id": "demucs.drums",  "url": "http://...wav",
        ///        "display_name": "Drums"},
        ///       {"id": "demucs.bass",   "url": "http://...wav",
        ///        "display_name": "Bass"} ] }
        public static let loadStems         = "load_stems"
    }
}
