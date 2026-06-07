//
// Connect — macOS audio companion (prototype CLI)
//
// Subcommands:
//   connect devices         — list available CoreAudio I/O devices
//   connect latency         — measure round-trip latency (impulse loopback)
//   connect monitor         — start passthrough monitoring (Ctrl-C to stop)
//   connect jam <stem-dir>  — load every .wav in stem-dir and play with monitoring
//
// Designed so each subcommand exits cleanly without requiring an
// AppKit run loop. We do most of the audio work on the main thread
// because AVAudioEngine doesn't need an NSApplication to run.
//

import AppKit
import AVFoundation
import ConnectCore
import CoreAudio
import Foundation

@main
struct Connect {
    @MainActor
    static func main() {
        // Strip Finder-injected process-serial-number argument so a
        // double-click launch (which arrives as "Connect -psn_0_…")
        // routes to the GUI rather than the CLI usage banner.
        let args = CommandLine.arguments.filter { !$0.hasPrefix("-psn_") }
        let cmd = args.count > 1 ? args[1] : "gui"
        switch cmd {
        case "devices":
            listDevices()
        case "latency":
            measureLatency()
        case "monitor":
            startMonitor()
        case "jam":
            let dir = args.count > 2 ? args[2] : ""
            startJam(stemDirectory: dir)
        case "bridge":
            // Positional args: bridge [session-id] [server-ws] [monitor-gain]
            // monitor-gain defaults to 0.0 (muted) so launching the bridge
            // without headphones can't feed laptop mic into laptop speakers.
            let sessionId = args.count > 2 ? args[2] : "default"
            let server = args.count > 3 ? args[3] : "ws://127.0.0.1:8000/ws/connect-bridge"
            let gain = (args.count > 4 ? Float(args[4]) : nil) ?? 0.0
            startBridge(sessionId: sessionId, serverURL: server, monitorGain: gain)
        case "gui":
            runGUI()
        case "help", "-h", "--help":
            printUsage()
        default:
            printUsage()
        }
    }

    // MARK: - GUI

    /// Launch the menu-bar app. Used for Finder / .app-bundle launches.
    /// Blocks until the user quits.
    @MainActor
    static func runGUI() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.activate(ignoringOtherApps: true)
        app.run()
    }

    // MARK: - devices

    static func listDevices() {
        print("CoreAudio devices:")
        var size: UInt32 = 0
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        guard AudioObjectGetPropertyDataSize(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size
        ) == noErr else {
            print("  (could not enumerate)")
            return
        }
        let count = Int(size) / MemoryLayout<AudioDeviceID>.size
        var ids = [AudioDeviceID](repeating: 0, count: count)
        guard AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids
        ) == noErr else { return }

        for id in ids {
            let nm = deviceName(id: id) ?? "(unnamed)"
            print("  [\(id)] \(nm) in=\(channelCount(id: id, input: true)) out=\(channelCount(id: id, input: false))")
        }
    }

    static func deviceName(id: AudioDeviceID) -> String? {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var name: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        let status = withUnsafeMutablePointer(to: &name) { ptr in
            AudioObjectGetPropertyData(id, &address, 0, nil, &size, ptr)
        }
        guard status == noErr, let cf = name?.takeRetainedValue() else { return nil }
        return cf as String
    }

    static func channelCount(id: AudioDeviceID, input: Bool) -> Int {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreamConfiguration,
            mScope: input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(id, &address, 0, nil, &size) == noErr else { return 0 }
        // The buffer list reports its own buffer count in the leading
        // mNumberBuffers field. The byte size is:
        //   sizeof(UInt32) + N * sizeof(AudioBuffer)
        // Bail out cleanly when N == 0 — allocate(maximumBuffers: 0) traps.
        let headerSize = MemoryLayout<UInt32>.size
        let perBuffer = MemoryLayout<AudioBuffer>.size
        guard Int(size) > headerSize else { return 0 }
        let bufferCount = (Int(size) - headerSize) / perBuffer
        guard bufferCount > 0 else { return 0 }

        let bufferList = AudioBufferList.allocate(maximumBuffers: bufferCount)
        defer { free(bufferList.unsafeMutablePointer) }
        guard AudioObjectGetPropertyData(
            id, &address, 0, nil, &size, bufferList.unsafeMutablePointer
        ) == noErr else { return 0 }
        var total = 0
        for buf in bufferList { total += Int(buf.mNumberChannels) }
        return total
    }

    // MARK: - latency

    static func measureLatency() {
        print("Latency probe — make sure output is audible to the input")
        print("(use a loopback cable, interface software loopback, or place mic near speaker).")
        do {
            let probe = LatencyProbe()
            let result = try probe.run()
            print(String(format: "  round-trip: %.2f ms  (peak=%.3f, confidence=%@)",
                         result.roundTripMs, result.inputPeakAmplitude, result.confidence))
            if result.confidence == "no_signal" {
                print("  No impulse detected. Check input gain and routing.")
            } else if result.roundTripMs > 30 {
                print("  WARN: round-trip > 30 ms. Real-time jamming will feel laggy.")
            } else if result.roundTripMs > 15 {
                print("  OK but not ideal. Aim for <15 ms.")
            } else {
                print("  Within target. Suitable for jam.")
            }
        } catch {
            print("  probe failed: \(error)")
        }
    }

    // MARK: - monitor

    static func startMonitor() {
        let engine = AudioEngine()
        engine.inputMonitorGain = 0.7
        do {
            try engine.start()
        } catch {
            print("Engine failed to start: \(error)")
            return
        }
        let report = engine.latencyReport()
        print("Monitor running. Ctrl-C to stop.")
        print(String(format: "  driver latency  in=%.2f ms  out=%.2f ms  buf=%.2f ms",
                     report.inputDeviceLatencySec * 1000,
                     report.outputDeviceLatencySec * 1000,
                     report.bufferDurationSec * 1000))
        print(String(format: "  estimated round-trip floor: %.2f ms",
                     report.estimatedRoundTripSec * 1000))
        RunLoop.current.run()
    }

    // MARK: - jam

    static func startJam(stemDirectory: String) {
        guard !stemDirectory.isEmpty else {
            print("Usage: connect jam <stem-directory>")
            return
        }
        let dir = URL(fileURLWithPath: stemDirectory)
        let engine = AudioEngine()
        engine.inputMonitorGain = 0.7
        engine.stemsGain = 0.9

        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) else {
            print("Could not read stem directory: \(dir.path)")
            return
        }
        let wavs = entries.filter { $0.pathExtension.lowercased() == "wav" }
        if wavs.isEmpty {
            print("No .wav files in \(dir.path)")
            return
        }
        for url in wavs {
            do {
                try engine.loadStem(name: url.deletingPathExtension().lastPathComponent, url: url)
                print("  loaded stem: \(url.lastPathComponent)")
            } catch {
                print("  failed to load \(url.lastPathComponent): \(error)")
            }
        }

        do { try engine.start() } catch {
            print("Engine failed to start: \(error)")
            return
        }
        engine.playAllStems(loop: false)
        print("Jamming. Ctrl-C to stop.")
        RunLoop.current.run()
    }

    static func printUsage() {
        print("""
        ToneForge Connect — macOS audio companion (prototype)

        Usage:
          connect devices                          List CoreAudio devices
          connect latency                          Measure round-trip audio latency
          connect monitor                          Run input → output passthrough
          connect jam <stem-directory>             Play .wav stems alongside monitoring
          connect bridge [session-id] [server-ws]  Pair with the web app and apply pushed tone presets
        """)
    }

    // MARK: - bridge

    /// Long-running mode: open input monitoring with the static amp-sim
    /// engaged, dial /ws/connect-bridge on the backend, and apply every
    /// preset payload the web app pushes onto the channel. The browser
    /// pushes on `analysis_complete`; the server replays the most-
    /// recent push to any joiner so order doesn't matter.
    static func startBridge(sessionId: String, serverURL: String, monitorGain: Float) {
        guard let url = URL(string: serverURL) else {
            print("bridge: invalid server URL \(serverURL)")
            return
        }

        // Clamp the requested monitor gain. 0.0 means muted — the safe
        // default when we can't be sure the user has headphones. If we
        // wired the mic into the speakers at unity on the average Mac,
        // we'd be guaranteed to feed back.
        let clamped = max(0.0, min(1.0, monitorGain))
        let engine = AudioEngine()
        engine.inputMonitorGain = clamped
        engine.ampSimEnabled = true
        do {
            try engine.start()
        } catch {
            print("bridge: engine failed to start: \(error)")
            return
        }

        let report = engine.latencyReport()
        print("Bridge running — session=\(sessionId)")
        print(String(format: "  monitor floor: %.2f ms (in=%.2f out=%.2f buf=%.2f)",
                     report.estimatedRoundTripSec * 1000,
                     report.inputDeviceLatencySec * 1000,
                     report.outputDeviceLatencySec * 1000,
                     report.bufferDurationSec * 1000))
        if clamped == 0.0 {
            print("  monitor: MUTED (default). To hear yourself through the matched tone,")
            print("           put on headphones and relaunch with: connect bridge \(sessionId) \(serverURL) 0.7")
        } else {
            print(String(format: "  monitor: gain=%.2f — WATCH FOR FEEDBACK without headphones", clamped))
        }

        let bridge = PresetBridge(serverURL: url, sessionId: sessionId)
        bridge.onStatus = { msg in
            print("  [bridge] \(msg)")
        }
        bridge.onPresetPush = { preset in
            // Pretty-print the match payload for sanity, then apply.
            let match = preset["match"] as? [String: Any]
            let name = (match?["preset_name"] as? String) ?? "(unknown)"
            let inst = (match?["instrument"] as? String) ?? "?"
            print("  [bridge] applying preset \"\(name)\" (\(inst))")
            engine.applyTonePreset(preset)
        }
        // Remote-controlled monitor gain. The browser's slider pushes
        // these. We don't second-guess: feedback safety is the user's
        // responsibility once they're driving the level.
        bridge.onGainChange = { gain in
            engine.inputMonitorGain = gain
            print(String(format: "  [bridge] monitor gain set to %.2f", gain))
        }
        bridge.start()

        // Keep the process alive until Ctrl-C.
        RunLoop.current.run()
    }
}
