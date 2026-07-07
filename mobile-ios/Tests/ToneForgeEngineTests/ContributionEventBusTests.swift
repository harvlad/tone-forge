// ContributionEventBusTests.swift
//
// Pins the bus contract: synchronous fan-out in subscription order,
// unsubscribe semantics, and re-entrancy safety (handlers may
// publish / subscribe / unsubscribe during fan-out; list mutations
// take effect for the NEXT publish).

import XCTest
@testable import ToneForgeEngine

@MainActor
final class ContributionEventBusTests: XCTestCase {

    private func makeEvent(row: Int = 1, col: Int = 1) -> ContributionEvent {
        ContributionEvent(
            source: .touch,
            kind: .padDown(row: row, col: col),
            timestamp: 0,
            hostTime: 0
        )
    }

    func testPublishIsSynchronous() {
        let bus = ContributionEventBus()
        var received = false
        bus.subscribe { _ in received = true }
        bus.publish(makeEvent())
        // No wait, no expectation: handler must have run inline.
        XCTAssertTrue(received)
    }

    func testFanOutInSubscriptionOrder() {
        let bus = ContributionEventBus()
        var order: [Int] = []
        bus.subscribe { _ in order.append(1) }
        bus.subscribe { _ in order.append(2) }
        bus.subscribe { _ in order.append(3) }
        bus.publish(makeEvent())
        XCTAssertEqual(order, [1, 2, 3])
    }

    func testEventPayloadDeliveredIntact() {
        let bus = ContributionEventBus()
        let sent = ContributionEvent(
            source: .launchpad,
            kind: .midiNote(note: 64, velocity: 90, on: true),
            timestamp: 7.5,
            hostTime: 555,
            velocity: 0.71,
            isReplay: true
        )
        var got: ContributionEvent?
        bus.subscribe { got = $0 }
        bus.publish(sent)
        XCTAssertEqual(got, sent)
    }

    func testUnsubscribeStopsDelivery() {
        let bus = ContributionEventBus()
        var count = 0
        let token = bus.subscribe { _ in count += 1 }
        bus.publish(makeEvent())
        bus.unsubscribe(token)
        bus.publish(makeEvent())
        XCTAssertEqual(count, 1)
    }

    func testUnsubscribeUnknownTokenIsNoOp() {
        let bus = ContributionEventBus()
        var count = 0
        bus.subscribe { _ in count += 1 }
        bus.unsubscribe(UUID())
        bus.publish(makeEvent())
        XCTAssertEqual(count, 1)
    }

    func testSubscribeDuringFanOutTakesEffectNextPublish() {
        let bus = ContributionEventBus()
        var lateCount = 0
        var didAdd = false
        bus.subscribe { [weak bus] _ in
            guard !didAdd else { return }
            didAdd = true
            bus?.subscribe { _ in lateCount += 1 }
        }
        bus.publish(makeEvent())
        XCTAssertEqual(lateCount, 0, "new subscriber must not see the in-flight event")
        bus.publish(makeEvent())
        XCTAssertEqual(lateCount, 1)
    }

    func testUnsubscribeSelfDuringFanOut() {
        let bus = ContributionEventBus()
        var count = 0
        var token: ContributionEventBus.Token?
        token = bus.subscribe { [weak bus] _ in
            count += 1
            if let t = token { bus?.unsubscribe(t) }
        }
        bus.publish(makeEvent())
        bus.publish(makeEvent())
        XCTAssertEqual(count, 1)
    }

    func testUnsubscribeLaterSubscriberDuringFanOutStillDeliversInFlight() {
        // The in-flight fan-out uses a snapshot: removing a later
        // subscriber mid-publish does not starve it of the current
        // event (predictable "mutations apply next publish" rule).
        let bus = ContributionEventBus()
        var secondCount = 0
        var secondToken: ContributionEventBus.Token?
        bus.subscribe { [weak bus] _ in
            if let t = secondToken { bus?.unsubscribe(t) }
        }
        secondToken = bus.subscribe { _ in secondCount += 1 }
        bus.publish(makeEvent())
        XCTAssertEqual(secondCount, 1)
        bus.publish(makeEvent())
        XCTAssertEqual(secondCount, 1)
    }

    func testReentrantPublishRunsNested() {
        let bus = ContributionEventBus()
        var log: [String] = []
        var republished = false
        bus.subscribe { [weak bus] event in
            if case .padDown(let row, _) = event.kind {
                log.append("A\(row)")
                if !republished {
                    republished = true
                    bus?.publish(ContributionEvent(
                        source: .touch,
                        kind: .padDown(row: 2, col: 1),
                        timestamp: 0,
                        hostTime: 0
                    ))
                }
            }
        }
        bus.subscribe { event in
            if case .padDown(let row, _) = event.kind { log.append("B\(row)") }
        }
        bus.publish(makeEvent(row: 1))
        // Depth-first: A1 → (nested A2, B2) → B1.
        XCTAssertEqual(log, ["A1", "A2", "B2", "B1"])
    }
}
