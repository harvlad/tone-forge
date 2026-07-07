// AudioTranscoder.swift
//
// Decode any AVFoundation-readable audio file (wav/mp3/m4a/aac/flac)
// and re-encode it as the canonical analysis upload format:
//
//   44.1 kHz, mono, 16-bit signed PCM, little-endian, in a WAV
//   container with a hand-rolled canonical 44-byte RIFF header.
//
// The header is hand-rolled (not AVAudioFile-written) so the output
// container is deterministic byte-for-byte for lossless inputs —
// that's what lets the golden-file tests compare exact bytes instead
// of parsing containers. AVAudioFile pads/extends chunks depending on
// OS version; a fixed 44-byte header does not.
//
// Threading: pure blocking function. Callers (ImportCoordinator) run
// it off the main actor via Task.detached.

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

public enum AudioTranscodeError: Error, LocalizedError, Equatable {
    case unreadableInput(String)
    case converterInitFailed
    case conversionFailed(String)
    case emptyOutput

    public var errorDescription: String? {
        switch self {
        case .unreadableInput(let detail):
            return "Couldn't read the audio file (\(detail))."
        case .converterInitFailed:
            return "Couldn't prepare the audio converter."
        case .conversionFailed(let detail):
            return "Audio conversion failed (\(detail))."
        case .emptyOutput:
            return "The audio file contained no samples."
        }
    }
}

public enum AudioTranscoder {

    /// Canonical analysis format constants.
    public static let sampleRate: Double = 44_100
    public static let channels: UInt32 = 1
    public static let bitsPerSample: UInt32 = 16
    /// Frames pumped through the converter per block. ~0.74 s of
    /// audio; keeps peak memory flat regardless of input length.
    static let blockFrames: UInt32 = 32_768

    public struct Result: Equatable, Sendable {
        /// Output frame count at 44.1 kHz mono.
        public let frameCount: Int
        public let durationSec: Double
        /// Total output file size (header + data), in bytes.
        public let byteCount: Int
    }

    #if canImport(AVFoundation)

    /// Transcode `input` to canonical analysis WAV at `output`.
    ///
    /// Writes to a sibling temp file and atomically replaces `output`
    /// on success, so a crash mid-write can never leave a truncated
    /// WAV at the destination path.
    @discardableResult
    public static func transcodeToAnalysisWAV(input: URL, output: URL) throws -> Result {
        let file: AVAudioFile
        do {
            file = try AVAudioFile(forReading: input)
        } catch {
            throw AudioTranscodeError.unreadableInput(error.localizedDescription)
        }
        let sourceFormat = file.processingFormat

        guard
            let targetFormat = AVAudioFormat(
                commonFormat: .pcmFormatInt16,
                sampleRate: sampleRate,
                channels: AVAudioChannelCount(channels),
                interleaved: true
            ),
            let converter = AVAudioConverter(from: sourceFormat, to: targetFormat)
        else {
            throw AudioTranscodeError.converterInitFailed
        }
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue

        // Stage into a temp file next to the destination so the final
        // move is atomic on the same volume.
        let tempURL = output.deletingLastPathComponent()
            .appendingPathComponent(".\(output.lastPathComponent).tmp-\(UUID().uuidString)")
        FileManager.default.createFile(atPath: tempURL.path, contents: nil)
        let handle = try FileHandle(forWritingTo: tempURL)
        defer {
            try? handle.close()
            try? FileManager.default.removeItem(at: tempURL)
        }

        // Placeholder header; size fields patched after the data pass.
        try handle.write(contentsOf: wavHeader(dataByteCount: 0))

        var readError: Error?
        var sawEOF = false
        let inputBlock: AVAudioConverterInputBlock = { _, outStatus in
            // Check the position explicitly: on recent OS releases,
            // AVAudioFile.read(into:) throws a spurious generic error
            // when called again at EOF instead of returning 0 frames.
            if sawEOF || file.framePosition >= file.length {
                sawEOF = true
                outStatus.pointee = .endOfStream
                return nil
            }
            guard
                let buffer = AVAudioPCMBuffer(
                    pcmFormat: sourceFormat, frameCapacity: AVAudioFrameCount(blockFrames)
                )
            else {
                outStatus.pointee = .endOfStream
                return nil
            }
            do {
                try file.read(into: buffer, frameCount: AVAudioFrameCount(blockFrames))
            } catch {
                readError = error
                outStatus.pointee = .endOfStream
                return nil
            }
            if buffer.frameLength == 0 {
                sawEOF = true
                outStatus.pointee = .endOfStream
                return nil
            }
            outStatus.pointee = .haveData
            return buffer
        }

        var dataByteCount = 0
        var frameCount = 0
        conversion: while true {
            guard
                let outBuffer = AVAudioPCMBuffer(
                    pcmFormat: targetFormat, frameCapacity: AVAudioFrameCount(blockFrames)
                )
            else {
                throw AudioTranscodeError.converterInitFailed
            }
            var convertError: NSError?
            let status = converter.convert(
                to: outBuffer, error: &convertError, withInputFrom: inputBlock
            )
            if let readError {
                throw AudioTranscodeError.conversionFailed(readError.localizedDescription)
            }
            if let convertError {
                throw AudioTranscodeError.conversionFailed(convertError.localizedDescription)
            }
            if outBuffer.frameLength > 0, let samples = outBuffer.int16ChannelData {
                let byteCount = Int(outBuffer.frameLength) * MemoryLayout<Int16>.size
                // Interleaved mono: all samples live in channel 0,
                // already little-endian on every Apple platform.
                let data = Data(bytes: samples[0], count: byteCount)
                try handle.write(contentsOf: data)
                dataByteCount += byteCount
                frameCount += Int(outBuffer.frameLength)
            }
            switch status {
            case .haveData, .inputRanDry:
                continue
            case .endOfStream:
                break conversion
            case .error:
                throw AudioTranscodeError.conversionFailed("converter reported error status")
            @unknown default:
                throw AudioTranscodeError.conversionFailed("unknown converter status")
            }
        }

        guard frameCount > 0 else { throw AudioTranscodeError.emptyOutput }

        // Patch the size fields now that the data length is known.
        try handle.seek(toOffset: 0)
        try handle.write(contentsOf: wavHeader(dataByteCount: dataByteCount))
        try handle.close()

        // Atomic replace at the destination.
        _ = try? FileManager.default.removeItem(at: output)
        try FileManager.default.moveItem(at: tempURL, to: output)

        return Result(
            frameCount: frameCount,
            durationSec: Double(frameCount) / sampleRate,
            byteCount: 44 + dataByteCount
        )
    }

    #endif

    /// Canonical 44-byte RIFF/WAVE header for 44.1 kHz mono s16le PCM.
    /// Pure function — unit-tested directly against known bytes.
    public static func wavHeader(dataByteCount: Int) -> Data {
        let byteRate = UInt32(sampleRate) * channels * bitsPerSample / 8
        let blockAlign = UInt16(channels * bitsPerSample / 8)

        var header = Data(capacity: 44)
        header.append(contentsOf: Array("RIFF".utf8))
        header.appendLE(UInt32(36 + dataByteCount))
        header.append(contentsOf: Array("WAVE".utf8))
        header.append(contentsOf: Array("fmt ".utf8))
        header.appendLE(UInt32(16))                     // fmt chunk size
        header.appendLE(UInt16(1))                      // PCM
        header.appendLE(UInt16(channels))
        header.appendLE(UInt32(sampleRate))
        header.appendLE(byteRate)
        header.appendLE(blockAlign)
        header.appendLE(UInt16(bitsPerSample))
        header.append(contentsOf: Array("data".utf8))
        header.appendLE(UInt32(dataByteCount))
        return header
    }
}

private extension Data {
    mutating func appendLE(_ value: UInt32) {
        var v = value.littleEndian
        Swift.withUnsafeBytes(of: &v) { append(contentsOf: $0) }
    }

    mutating func appendLE(_ value: UInt16) {
        var v = value.littleEndian
        Swift.withUnsafeBytes(of: &v) { append(contentsOf: $0) }
    }
}
