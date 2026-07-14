// SequencerClockTests.swift
//
// Pins SequencerClock step math: step advance, looping, one-shot
// stop, swing parity (odd steps delayed), stepPhase across loops,
// and stepInfo startTime using the unwrapped step index.

import XCTest
@testable import ToneForgeEngine

final class SequencerClockTests: XCTestCase {

    // 120 BPM → 16th-note step = 0.125 s.
    private func makeClock(stepCount: Int = 16, bpm: Double = 120) -> SequencerClock {
        SequencerClock(stepCount: stepCount, bpm: bpm)
    }

    // MARK: - Basics

    func testStepDurationAt120BPM() {
        let clock = makeClock()
        XCTAssertEqual(clock.stepDuration, 0.125, accuracy: 1e-9)
    }

    func testTickAdvancesSteps() {
        let clock = makeClock()
        clock.start(at: 10)
        XCTAssertEqual(clock.tick(songSeconds: 10.0), 0)
        XCTAssertEqual(clock.tick(songSeconds: 10.126), 1)
        XCTAssertEqual(clock.tick(songSeconds: 10.51), 4)
    }

    func testTickBeforeStartTimeStaysOnCurrentStep() {
        let clock = makeClock()
        clock.start(at: 10)
        XCTAssertEqual(clock.tick(songSeconds: 9.0), 0)
    }

    func testLoopWrapsAndNotifiesDelegate() {
        let clock = makeClock(stepCount: 4)
        let spy = DelegateSpy()
        clock.delegate = spy
        clock.start(at: 0)
        clock.tick(songSeconds: 0)
        // Step 5 raw = wrapped step 1, one loop boundary crossed.
        clock.tick(songSeconds: 0.126) // step 1
        clock.tick(songSeconds: 0.51)  // raw 4 → wrapped 0, loop
        XCTAssertEqual(clock.currentStep, 0)
        XCTAssertEqual(spy.loopCount, 1)
    }

    func testOneShotStopsAfterLastStep() {
        let clock = makeClock(stepCount: 4)
        clock.isLooping = false
        clock.start(at: 0)
        XCTAssertEqual(clock.tick(songSeconds: 0.376), 3) // last step
        XCTAssertNil(clock.tick(songSeconds: 0.51))       // past end
        XCTAssertFalse(clock.isRunning)
    }

    // MARK: - Swing

    func testSwingDelaysOddStepsOnly() {
        let clock = makeClock()
        clock.swing = 0.5
        // On-beats unswung.
        XCTAssertEqual(clock.triggerTime(forStep: 0, from: 0), 0, accuracy: 1e-9)
        XCTAssertEqual(clock.triggerTime(forStep: 2, from: 0), 0.25, accuracy: 1e-9)
        // Off-beat 16ths delayed by swing × stepDuration.
        XCTAssertEqual(clock.triggerTime(forStep: 1, from: 0), 0.125 + 0.0625, accuracy: 1e-9)
        XCTAssertEqual(clock.triggerTime(forStep: 3, from: 0), 0.375 + 0.0625, accuracy: 1e-9)
    }

    func testSwingClampedToHalf() {
        let clock = makeClock()
        clock.swing = 2.0
        XCTAssertEqual(clock.swing, 0.5)
        clock.swing = -1
        XCTAssertEqual(clock.swing, 0)
    }

    func testTickHoldsOddStepUntilSwungBoundary() {
        // swing 0.5 at 120 BPM: step 1's boundary moves from 0.125
        // to (1 + 0.5) × 0.125 = 0.1875.
        let clock = makeClock()
        clock.swing = 0.5
        clock.start(at: 0)
        XCTAssertEqual(clock.tick(songSeconds: 0.126), 0) // straight boundary: held
        XCTAssertEqual(clock.tick(songSeconds: 0.186), 0) // still before swung start
        XCTAssertEqual(clock.tick(songSeconds: 0.188), 1) // swung boundary passed
    }

    func testTickEvenStepsUnaffectedBySwing() {
        let clock = makeClock()
        clock.swing = 0.5
        clock.start(at: 0)
        XCTAssertEqual(clock.tick(songSeconds: 0.251), 2)
        XCTAssertEqual(clock.tick(songSeconds: 0.501), 4)
    }

    func testTickSwingHoldsOddStepAcrossLoop() {
        // stepCount 4: raw step 5 = wrapped step 1 in the second
        // pass. Swung start = (5 + 0.5) × 0.125 = 0.6875.
        let clock = makeClock(stepCount: 4)
        clock.swing = 0.5
        clock.start(at: 0)
        XCTAssertEqual(clock.tick(songSeconds: 0.63), 0)   // raw 5 held → wrapped 0
        XCTAssertEqual(clock.tick(songSeconds: 0.69), 1)   // swung boundary passed
    }

    // MARK: - Phase

    func testStepPhaseWithinFirstStep() {
        let clock = makeClock()
        clock.start(at: 0)
        XCTAssertEqual(clock.stepPhase(at: 0.0625), 0.5, accuracy: 1e-9)
    }

    func testStepPhaseCorrectAfterLoop() {
        let clock = makeClock(stepCount: 4)
        clock.start(at: 0)
        // Second loop pass (raw step 5), halfway into the step:
        // elapsed = 5.5 × 0.125 = 0.6875.
        clock.tick(songSeconds: 0.6875)
        XCTAssertEqual(clock.stepPhase(at: 0.6875), 0.5, accuracy: 1e-9)
    }

    // MARK: - StepInfo

    func testStepInfoStartTimeAfterLoop() {
        let clock = makeClock(stepCount: 4)
        clock.start(at: 10)
        // Raw step 5 (second loop, wrapped step 1): starts at
        // 10 + 5 × 0.125 = 10.625.
        let info = clock.stepInfo(at: 10.7)
        XCTAssertEqual(info?.step, 1)
        XCTAssertEqual(info?.startTime ?? -1, 10.625, accuracy: 1e-9)
        XCTAssertEqual(info?.phase ?? -1, (10.7 - 10.625) / 0.125, accuracy: 1e-9)
    }

    func testStepInfoBeforeStart() {
        let clock = makeClock()
        clock.start(at: 10)
        let info = clock.stepInfo(at: 9)
        XCTAssertEqual(info?.step, 0)
        XCTAssertEqual(info?.phase, 0)
        XCTAssertEqual(info?.startTime, 10)
    }

    func testStepInfoNilWhenStopped() {
        let clock = makeClock()
        XCTAssertNil(clock.stepInfo(at: 0))
    }

    // MARK: - Resume

    func testStartFromStepResumesAtBoundary() {
        let clock = makeClock(stepCount: 8)
        clock.start(at: 5, fromStep: 3)
        XCTAssertEqual(clock.currentStep, 3)
        XCTAssertEqual(clock.tick(songSeconds: 5.0), 3)
        XCTAssertEqual(clock.tick(songSeconds: 5.126), 4)
    }

    func testStartFromStepClampsToStepCount() {
        let clock = makeClock(stepCount: 4)
        clock.start(at: 0, fromStep: 99)
        XCTAssertEqual(clock.currentStep, 3)
    }

    // MARK: - Config

    func testStepCountShrinkResetsOutOfRangeStep() {
        let clock = makeClock(stepCount: 16)
        clock.start(at: 0)
        clock.tick(songSeconds: 1.26) // step 10
        XCTAssertEqual(clock.currentStep, 10)
        clock.stepCount = 8
        XCTAssertEqual(clock.currentStep, 0)
    }

    func testBPMChangeUpdatesStepDuration() {
        let clock = makeClock()
        clock.bpm = 60
        XCTAssertEqual(clock.stepDuration, 0.25, accuracy: 1e-9)
    }
}

// MARK: - Delegate spy

private final class DelegateSpy: SequencerClockDelegate {
    var advances: [Int] = []
    var loopCount = 0

    func sequencerClock(_ clock: SequencerClock, didAdvanceTo step: Int, isDownbeat: Bool) {
        advances.append(step)
    }

    func sequencerClockDidLoop(_ clock: SequencerClock) {
        loopCount += 1
    }
}
