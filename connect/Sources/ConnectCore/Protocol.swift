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
    public static let version: Int = 1

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
    }
}
