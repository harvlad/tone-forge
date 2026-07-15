// Harvester.swift
//
// Stage B of the E-GMD data pipeline. Reads the onset manifest emitted
// by `backend/scripts/harvest_egmd.py` (rows: wav_path,onset_sec,role),
// decodes each audio file to canonical 48 kHz mono, slices a window at
// every onset using the SAME bounds `BeatOnsetExtractor` feeds the
// classifier at runtime, extracts `OnsetFeatures`, and writes a
// corrections-format CSV the trainer merges via `--harvest`.
//
// Feature parity is the whole point: extraction goes through
// `OnsetFeatures.extract`, identical to inference — a Python
// re-implementation would drift and silently poison the model.

import Foundation
import AVFoundation
import ToneForgeEngine

enum Harvester {

    /// Canonical rate every slice is extracted at (matches runtime).
    static let sampleRate = 48_000.0
    /// Slice cap in seconds — mirrors `BeatOnsetExtractor.maxSliceSec`.
    static let maxSliceSec = 0.14

    private struct Row { let wav: String; let onset: Double; let role: String }

    /// Convert a manifest into a corrections CSV. Best-effort: an
    /// unreadable file or bad row is warned + skipped, never fatal.
    static func run(manifestPath: String, audioRoot: String?, outPath: String) {
        let rows = loadManifest(manifestPath)
        if rows.isEmpty {
            FileHandle.standardError.write(Data("harvest: no manifest rows\n".utf8))
        }

        // Group onsets by file so each wav decodes exactly once.
        var byFile: [String: [Row]] = [:]
        for r in rows { byFile[r.wav, default: []].append(r) }

        let names = OnsetFeatures.featureNames
        let timestamp = ISO8601DateFormatter().string(from: Date())

        var out = names.joined(separator: ",") + ",original,corrected,timestamp\n"
        var written = 0
        var roleCounts: [String: Int] = [:]

        for (wav, group) in byFile {
            guard let samples = decodeMono48k(path: wav, audioRoot: audioRoot) else {
                FileHandle.standardError.write(Data("harvest: skip unreadable \(wav)\n".utf8))
                continue
            }
            let n = samples.count
            guard n > 0 else { continue }

            // Ascending onsets so "cap at next onset" matches runtime.
            let onsets = group.sorted { $0.onset < $1.onset }
            let maxLen = Int(maxSliceSec * sampleRate)
            for (i, row) in onsets.enumerated() {
                let start = Int((row.onset * sampleRate).rounded())
                guard start >= 0, start < n else { continue }
                let nextStart = i + 1 < onsets.count
                    ? Int((onsets[i + 1].onset * sampleRate).rounded())
                    : n
                let end = min(nextStart, start + maxLen, n)
                guard end > start else { continue }
                let slice = Array(samples[start..<end])
                let feat = OnsetFeatures.extract(slice, sampleRate: sampleRate)
                let cells = feat.featureVector.map { String($0) }
                out += cells.joined(separator: ",")
                    + ",\(row.role),\(row.role),\(timestamp)\n"
                written += 1
                roleCounts[row.role, default: 0] += 1
            }
        }

        do {
            try out.write(toFile: outPath, atomically: true, encoding: .utf8)
        } catch {
            FileHandle.standardError.write(Data("harvest: write failed: \(error)\n".utf8))
            exit(1)
        }

        let hist = OnsetFeaturesRoles.all
            .map { "\($0)=\(roleCounts[$0] ?? 0)" }
            .joined(separator: "  ")
        print("Harvested \(written) rows -> \(outPath)")
        print("  \(hist)")
    }

    // MARK: - Manifest

    private static func loadManifest(_ path: String) -> [Row] {
        guard let text = try? String(contentsOfFile: path, encoding: .utf8) else {
            FileHandle.standardError.write(Data("harvest: cannot read \(path)\n".utf8))
            return []
        }
        let lines = text.split(whereSeparator: \.isNewline)
        guard lines.count > 1 else { return [] }
        let header = lines[0].split(separator: ",").map(String.init)
        guard let wi = header.firstIndex(of: "wav_path"),
              let oi = header.firstIndex(of: "onset_sec"),
              let ri = header.firstIndex(of: "role") else {
            FileHandle.standardError.write(Data("harvest: bad manifest header\n".utf8))
            return []
        }
        var out: [Row] = []
        for line in lines.dropFirst() {
            let cols = line.split(separator: ",", omittingEmptySubsequences: false)
                .map(String.init)
            guard cols.count > max(wi, oi, ri),
                  let onset = Double(cols[oi]) else { continue }
            out.append(Row(wav: cols[wi], onset: onset, role: cols[ri]))
        }
        return out
    }

    // MARK: - Audio decode (mono, 48 kHz)

    /// Decode any AVFoundation-readable audio file to a mono 48 kHz
    /// `[Float]`. Resamples with the highest-quality converter so
    /// features match a natively-48k runtime buffer. Returns nil on
    /// any failure.
    private static func decodeMono48k(path: String, audioRoot: String?) -> [Float]? {
        let url = resolve(path, audioRoot: audioRoot)
        guard let file = try? AVAudioFile(forReading: url) else { return nil }

        let srcFormat = file.processingFormat
        let srcFrames = AVAudioFrameCount(file.length)
        guard srcFrames > 0,
              let inBuf = AVAudioPCMBuffer(pcmFormat: srcFormat, frameCapacity: srcFrames)
        else { return nil }
        do { try file.read(into: inBuf) } catch { return nil }

        // Already mono @ 48k? Copy straight out.
        if srcFormat.sampleRate == sampleRate, srcFormat.channelCount == 1,
           let ch = inBuf.floatChannelData {
            return Array(UnsafeBufferPointer(start: ch[0], count: Int(inBuf.frameLength)))
        }

        guard let outFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: sampleRate, channels: 1, interleaved: false
        ), let converter = AVAudioConverter(from: srcFormat, to: outFormat) else {
            return nil
        }
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue

        let ratio = sampleRate / srcFormat.sampleRate
        let outCap = AVAudioFrameCount(Double(inBuf.frameLength) * ratio) + 4096
        guard let outBuf = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: outCap)
        else { return nil }

        var supplied = false
        var convErr: NSError?
        let status = converter.convert(to: outBuf, error: &convErr) { _, outStatus in
            if supplied {
                outStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            outStatus.pointee = .haveData
            return inBuf
        }
        guard status != .error, convErr == nil, let ch = outBuf.floatChannelData else {
            return nil
        }
        return Array(UnsafeBufferPointer(start: ch[0], count: Int(outBuf.frameLength)))
    }

    private static func resolve(_ path: String, audioRoot: String?) -> URL {
        if path.hasPrefix("/") { return URL(fileURLWithPath: path) }
        if let root = audioRoot {
            return URL(fileURLWithPath: root).appendingPathComponent(path)
        }
        return URL(fileURLWithPath: path)
    }
}

/// The canonical drum-role raw values, for histogram ordering. Kept
/// local so the harvester never depends on CoreML.
private enum OnsetFeaturesRoles {
    static let all = DrumRole.allCases.map { $0.rawValue }
}
