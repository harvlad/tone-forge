// ContributionEventBus.swift
//
// Synchronous @MainActor fan-out for ContributionEvents. This is the
// single funnel between input surfaces and the audio executors:
//
//   TouchGridOverlay ─┐
//   USBLaunchpadTransport ─┼→ bus.publish(event) → subscribers
//   SessionPlayer (isReplay) ─┘        ├─ ModeCoordinator → ModeRouter → engine
//                                      └─ SessionCaptureRecorder (P6)
//
// Design notes:
//   - SYNCHRONOUS fan-out, subscription order. No queueing, no async
//     hop — publish() runs every handler inline so the bus itself adds
//     zero latency to the pad-tap→attack budget (≤8 ms). Producers on
//     other threads (CoreMIDI) stamp `timestamp` + `hostTime` BEFORE
//     their DispatchQueue.main.async hop; the hop is the only added
//     latency and the probe measures it via hostTime.
//   - Re-entrancy safe: a handler may publish() (e.g. a mode change
//     triggered by an outer Launchpad button re-emitting pad state) or
//     subscribe/unsubscribe during fan-out. Mutations during fan-out
//     take effect for the NEXT publish — the in-flight fan-out uses a
//     snapshot of the subscriber list.
//   - Tokens are opaque UUIDs; unsubscribe with an unknown token is a
//     no-op.

import Foundation

@MainActor
public final class ContributionEventBus {

    public typealias Token = UUID

    /// Ordered subscriber list. Array (not Dictionary) so fan-out
    /// order == subscription order, which tests pin down.
    private var subscribers: [(token: Token, handler: (ContributionEvent) -> Void)] = []

    public init() {}

    /// Register a handler. Handlers run synchronously on the main
    /// actor in subscription order for every published event.
    @discardableResult
    public func subscribe(_ handler: @escaping (ContributionEvent) -> Void) -> Token {
        let token = UUID()
        subscribers.append((token, handler))
        return token
    }

    /// Remove a handler. Unknown tokens are ignored. Safe to call
    /// from within a handler (takes effect next publish).
    public func unsubscribe(_ token: Token) {
        subscribers.removeAll { $0.token == token }
    }

    /// Fan `event` out to every subscriber, synchronously, in
    /// subscription order. Re-entrant publishes from handlers run
    /// nested (depth-first) — acceptable because handlers are cheap
    /// executors and re-entrant publishing is rare.
    public func publish(_ event: ContributionEvent) {
        let snapshot = subscribers
        for entry in snapshot {
            entry.handler(event)
        }
    }
}
