// UITestSupport.swift
//
// Launch-argument contract for the UI test target. Two hooks:
//
//   -uitest-reset-attestation
//       Clears the persisted ownership attestation before the UI
//       builds, so tests always start from the un-attested state.
//       Handled in ToneForgeScene.init.
//
//   -uitest-stub-import
//       LibraryView shows a "UITest Import" row that drives the real
//       ImportCoordinator (attestation gate included) with a tiny
//       baked WAV and a stubbed analyze transport — no network, no
//       Music-library permission dialogs.
//
// Neither flag does anything unless explicitly passed at launch, and
// the analyze stub is only reachable through them.

import Foundation
import ToneForgeEngine

enum UITestSupport {

    static var resetAttestationRequested: Bool {
        ProcessInfo.processInfo.arguments.contains("-uitest-reset-attestation")
    }

    static var stubImportEnabled: Bool {
        ProcessInfo.processInfo.arguments.contains("-uitest-stub-import")
    }

    /// Transport for LibraryView's ImportCoordinator: the real job
    /// client normally, the never-finishing stub under UI test.
    static func makeJobClient() -> any JobSubmitting {
        stubImportEnabled ? StubJobClient() : BackendJobClient()
    }

    /// Write a canonical 0.1 s 440 Hz sine (44.1 kHz mono 16-bit WAV)
    /// into tmp so the import pipeline's transcode step has real audio
    /// to chew on. Deterministic, ~8.7 KB.
    static func writeStubWAV() throws -> URL {
        let sampleRate = 44_100
        let frames = sampleRate / 10
        var samples = [Int16](repeating: 0, count: frames)
        for i in 0..<frames {
            let phase = 2.0 * Double.pi * 440.0 * Double(i) / Double(sampleRate)
            samples[i] = Int16(sin(phase) * 8000.0)
        }

        var data = Data()
        func append(_ value: UInt32) {
            withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) }
        }
        func append(_ value: UInt16) {
            withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) }
        }
        let dataBytes = UInt32(frames * 2)
        data.append(contentsOf: Array("RIFF".utf8))
        append(36 + dataBytes)
        data.append(contentsOf: Array("WAVE".utf8))
        data.append(contentsOf: Array("fmt ".utf8))
        append(UInt32(16))
        append(UInt16(1))                       // PCM
        append(UInt16(1))                       // mono
        append(UInt32(sampleRate))
        append(UInt32(sampleRate * 2))          // byte rate
        append(UInt16(2))                       // block align
        append(UInt16(16))                      // bits per sample
        data.append(contentsOf: Array("data".utf8))
        append(dataBytes)
        samples.withUnsafeBytes { data.append(contentsOf: $0) }

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("uitest-stub-import.wav")
        try data.write(to: url, options: [.atomic])
        return url
    }
}

/// Job transport that submits a fake job and reports progress without
/// ever completing: the attestation UI tests only need the Analysing
/// sheet to be visible. Cancellation (Cancel button / coordinator
/// dismiss) terminates the stream via task cancellation.
struct StubJobClient: JobSubmitting {
    func submit(
        baseURL: URL, wavFileURL: URL, filename: String
    ) async throws -> String {
        "uitest-stub-job"
    }

    func events(
        baseURL: URL, jobId: String
    ) -> AsyncThrowingStream<AnalyzeEvent, Error> {
        AsyncThrowingStream { continuation in
            continuation.yield(.progress(message: "Analysing (stub)…", percent: 42))
            // Intentionally never finished — see doc comment.
        }
    }
}
