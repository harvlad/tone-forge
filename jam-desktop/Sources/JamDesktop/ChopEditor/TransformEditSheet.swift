// TransformEditSheet.swift
//
// Desktop P4 transform editor sheet: edits a pad's transform chain
// (render-on-arm), previews the result, and bakes to a new local
// sample. Port of iOS PadTransformSection.
//
// Chain edits push through SessionController → PadTransformHost
// (persistence, render, loop flags, grid badges all follow). "Bake"
// renders the chain → classifies → saves to PadSampleStore → pad
// reassigns to the baked sample with cleared chain. Non-destructive.

import SwiftUI
import AVFoundation
import ToneForgeEngine
import JamDesktopCore
import JamDesktopAudio

/// Target for the transform editor: which pad, its assignment, and
/// the stem file for loading the base buffer.
struct TransformEditTarget: Identifiable {
    let id = UUID()
    let pad: LaunchpadPad
    let assignment: PadAssignment
    let stemURL: URL
    let analysisId: String
}

struct TransformEditSheet: View {
    let target: TransformEditTarget

    @EnvironmentObject private var session: SessionController
    @Environment(\.dismiss) private var dismiss

    @State private var chain: [PadTransform] = []
    @State private var bakeState: BakeState = .idle

    private enum BakeState: Equatable {
        case idle, baking, done, failed(String)
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    chainList
                    addMenu
                    if !chain.isEmpty {
                        bakeControls
                    }
                }
                .padding(16)
            }
        }
        .frame(minWidth: 380, minHeight: 400)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        .onAppear {
            chain = session.transformHost.loops(
                packId: target.analysisId, padIdx: target.assignment.chop.idx
            ) ? [.loop] : []
            // TODO: load persisted chain from assignment store when added
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Transforms")
                    .font(.title3.bold())
                Text(target.assignment.chop.chordSymbol
                     ?? target.assignment.chop.sectionLabel
                     ?? "Chop \(target.assignment.chop.idx + 1)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Done") { dismiss() }
                .keyboardShortcut(.defaultAction)
        }
        .padding(16)
    }

    // MARK: - Chain list

    private var chainList: some View {
        VStack(spacing: 8) {
            ForEach(Array(chain.enumerated()), id: \.offset) { idx, transform in
                HStack {
                    row(for: transform, at: idx)
                    Spacer()
                    Button {
                        chain.remove(at: idx)
                        pushChain()
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
                .padding(8)
                .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 6))
            }
        }
    }

    @ViewBuilder
    private func row(for transform: PadTransform, at idx: Int) -> some View {
        switch transform {
        case .reverse, .harmony, .choir, .loop:
            Text(Self.label(transform))

        case .stutter(let rate):
            HStack {
                Text("Stutter")
                Picker("", selection: Binding(
                    get: { rate },
                    set: { replace(idx, with: .stutter($0)) }
                )) {
                    ForEach(StutterRate.allCases, id: \.self) { r in
                        Text(Self.rateLabel(r)).tag(r)
                    }
                }
                .frame(width: 80)
            }

        case .stretch(let factor):
            VStack(alignment: .leading, spacing: 4) {
                Text(String(format: "Stretch %.2f\u{00D7}", factor))
                Slider(
                    value: Binding(
                        get: { factor },
                        set: { replace(idx, with: .stretch($0)) }
                    ),
                    in: 0.25...4
                )
            }

        case .octave(let n):
            Stepper(
                "Octave \(n > 0 ? "+\(n)" : "\(n)")",
                value: Binding(
                    get: { n },
                    set: { replace(idx, with: .octave($0)) }
                ),
                in: -2...2
            )

        case .gate(let steps):
            VStack(alignment: .leading, spacing: 6) {
                Text("Gate (16 steps)")
                gateGrid(steps: steps, at: idx)
            }

        case .granular(let params):
            DisclosureGroup("Granular") {
                paramSlider(
                    "Grain", value: params.grainMs, range: 10...300,
                    format: "%.0f ms"
                ) { v in
                    var p = params; p.grainMs = v
                    replace(idx, with: .granular(p))
                }
                paramSlider(
                    "Density", value: params.densityHz, range: 2...80,
                    format: "%.0f /s"
                ) { v in
                    var p = params; p.densityHz = v
                    replace(idx, with: .granular(p))
                }
                paramSlider(
                    "Jitter", value: params.positionJitter, range: 0...1,
                    format: "%.2f"
                ) { v in
                    var p = params; p.positionJitter = v
                    replace(idx, with: .granular(p))
                }
                paramSlider(
                    "Pitch spread", value: params.pitchSpreadSemis,
                    range: 0...12, format: "%.1f st"
                ) { v in
                    var p = params; p.pitchSpreadSemis = v
                    replace(idx, with: .granular(p))
                }
            }

        case .spectralFreeze(let atSec, let seed):
            VStack(alignment: .leading, spacing: 4) {
                Text(String(format: "Freeze at %.2f s", atSec))
                Slider(
                    value: Binding(
                        get: { atSec },
                        set: {
                            replace(idx, with: .spectralFreeze(
                                atSec: $0, seed: seed
                            ))
                        }
                    ),
                    in: 0...8
                )
            }
        }
    }

    private func gateGrid(steps: [Bool], at idx: Int) -> some View {
        let columns = Array(repeating: GridItem(.flexible(), spacing: 4), count: 8)
        return LazyVGrid(columns: columns, spacing: 4) {
            ForEach(0..<16, id: \.self) { step in
                let on = step < steps.count && steps[step]
                Button {
                    var s = Self.padded(steps)
                    s[step].toggle()
                    replace(idx, with: .gate(steps: s))
                } label: {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(on ? Color.accentColor : Color(white: 0.25))
                        .frame(height: 18)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func paramSlider(
        _ title: String,
        value: Double,
        range: ClosedRange<Double>,
        format: String,
        onChange: @escaping (Double) -> Void
    ) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title)
                Spacer()
                Text(String(format: format, value))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            Slider(
                value: Binding(get: { value }, set: onChange),
                in: range
            )
        }
    }

    // MARK: - Add menu

    private var addMenu: some View {
        Menu {
            addButton(.reverse)
            addButton(.stutter(.r1_8))
            addButton(.granular(GranularParams()))
            addButton(.stretch(2.0))
            addButton(.octave(1))
            addButton(.harmony)
            addButton(.choir)
            addButton(.gate(steps: Self.defaultGateSteps))
            if !chain.contains(.loop) {
                addButton(.loop)
            }
            addButton(.spectralFreeze(
                atSec: 0.25, seed: UInt64.random(in: 0..<UInt64.max)
            ))
        } label: {
            Label("Add transform", systemImage: "plus.circle")
        }
        .menuStyle(.borderlessButton)
    }

    private func addButton(_ transform: PadTransform) -> some View {
        Button(Self.label(transform)) {
            chain.append(transform)
            pushChain()
        }
    }

    // MARK: - Bake controls

    @ViewBuilder
    private var bakeControls: some View {
        Divider().padding(.vertical, 8)

        switch bakeState {
        case .baking:
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text("Baking...").foregroundStyle(.secondary)
            }
        default:
            Button {
                bake()
            } label: {
                Label("Bake to new sample", systemImage: "flame")
            }
            .buttonStyle(.borderedProminent)

            if case .failed(let message) = bakeState {
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
            if bakeState == .done {
                Label("Baked", systemImage: "checkmark.circle.fill")
                    .font(.footnote)
                    .foregroundStyle(.green)
            }
        }

        Text("Applied in order, re-rendered on edit. Bake saves the result as a new local sample.")
            .font(.caption)
            .foregroundStyle(.secondary)
    }

    private func bake() {
        bakeState = .baking
        Task {
            do {
                // Load base buffer from stem file segment
                guard let file = try? AVAudioFile(forReading: target.stemURL) else {
                    bakeState = .failed("Could not open stem file")
                    return
                }
                guard let baseBuffer = await session.transformBakeService.loadBuffer(
                    file: file,
                    startSec: target.assignment.chop.startSec,
                    endSec: target.assignment.chop.endSec
                ) else {
                    bakeState = .failed("Could not load audio segment")
                    return
                }

                // Bake: render → classify → save
                let tempoBpm = session.sequencer.songBPM
                _ = try await session.transformBakeService.bake(
                    chain: chain,
                    baseBuffer: baseBuffer,
                    tempoBpm: tempoBpm,
                    chord: [],  // TODO: pass current chord
                    sourceProvenance: nil  // bundle chop = songChop
                )

                chain = []
                bakeState = .done
            } catch {
                bakeState = .failed(error.localizedDescription)
            }
        }
    }

    // MARK: - Helpers

    private func replace(_ idx: Int, with transform: PadTransform) {
        guard chain.indices.contains(idx) else { return }
        chain[idx] = transform
        pushChain()
    }

    private func pushChain() {
        // Arm the transform on the host for preview
        Task {
            guard let file = try? AVAudioFile(forReading: target.stemURL) else { return }
            guard let baseBuffer = await session.transformBakeService.loadBuffer(
                file: file,
                startSec: target.assignment.chop.startSec,
                endSec: target.assignment.chop.endSec
            ) else { return }

            session.transformHost.setChain(
                chain,
                packId: target.analysisId,
                padIdx: target.assignment.chop.idx,
                base: baseBuffer,
                tempoBpm: session.sequencer.songBPM,
                chord: []
            )
        }
    }

    static var defaultGateSteps: [Bool] {
        (0..<16).map { $0 % 2 == 0 }
    }

    static func padded(_ steps: [Bool]) -> [Bool] {
        var s = steps
        if s.count < 16 { s += Array(repeating: false, count: 16 - s.count) }
        return Array(s.prefix(16))
    }

    static func label(_ transform: PadTransform) -> String {
        switch transform {
        case .reverse:            return "Reverse"
        case .stutter(let r):     return "Stutter \(rateLabel(r))"
        case .granular:           return "Granular"
        case .stretch(let f):     return String(format: "Stretch %.2f\u{00D7}", f)
        case .octave(let n):      return "Octave \(n > 0 ? "+\(n)" : "\(n)")"
        case .harmony:            return "Harmony"
        case .choir:              return "Choir"
        case .gate:               return "Gate"
        case .loop:               return "Loop"
        case .spectralFreeze:     return "Spectral freeze"
        }
    }

    static func rateLabel(_ rate: StutterRate) -> String {
        switch rate {
        case .r1_4:  return "1/4"
        case .r1_8:  return "1/8"
        case .r1_16: return "1/16"
        case .r1_32: return "1/32"
        }
    }
}
