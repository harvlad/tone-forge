// PadTransformSection.swift
//
// The P4 transforms editor, shared by both long-press sheets
// (PadSourceSheet's manage view for local pads, PadEffectsEditor for
// pack pads). Edits are pushed straight through
// ModeCoordinator.setTransformChain — persistence, render-on-arm,
// loop flags, and grid badges all follow from that one call; the
// section keeps only a scratch copy of the chain for SwiftUI
// bindings.
//
// "Bake" renders the chain into a NEW local sample (classify → save
// → reassign, chain cleared) via ModeCoordinator.bakeTransforms —
// non-destructive, the original audio never changes.

import SwiftUI
import ToneForgeEngine

struct PadTransformSection: View {
    @EnvironmentObject private var appState: AppState

    /// PadIndex rawValue of the grid pad being edited.
    let gridRaw: Int

    @State private var chain: [PadTransform] = []

    private enum BakeState: Equatable {
        case idle, baking, done, failed(String)
    }
    @State private var bakeState: BakeState = .idle

    var body: some View {
        Section {
            ForEach(Array(chain.enumerated()), id: \.offset) { idx, transform in
                row(for: transform, at: idx)
            }
            .onDelete { offsets in
                chain.remove(atOffsets: offsets)
                push()
            }

            addMenu

            if !chain.isEmpty {
                bakeControls
            }
        } header: {
            Text("Transforms")
        } footer: {
            if !chain.isEmpty {
                Text("Applied in order, re-rendered on edit. Bake saves the result as a new local sample on this pad.")
            }
        }
        .onAppear {
            chain = appState.modeCoordinator.transformChain(gridPad: gridRaw)
        }
    }

    // MARK: - Rows

    @ViewBuilder
    private func row(for transform: PadTransform, at idx: Int) -> some View {
        switch transform {
        case .reverse, .harmony, .choir, .loop:
            Text(Self.label(transform))

        case .stutter(let rate):
            Picker("Stutter", selection: Binding(
                get: { rate },
                set: { replace(idx, with: .stutter($0)) }
            )) {
                ForEach(StutterRate.allCases, id: \.self) { r in
                    Text(Self.rateLabel(r)).tag(r)
                }
            }

        case .stretch(let factor):
            VStack(alignment: .leading) {
                Text(String(format: "Stretch %.2f×", factor))
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
            VStack(alignment: .leading) {
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
        let columns = Array(
            repeating: GridItem(.flexible(), spacing: 4), count: 8
        )
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

    // MARK: - Add / bake

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
    }

    private func addButton(_ transform: PadTransform) -> some View {
        Button(Self.label(transform)) {
            chain.append(transform)
            push()
        }
    }

    @ViewBuilder
    private var bakeControls: some View {
        switch bakeState {
        case .baking:
            HStack(spacing: 8) {
                ProgressView()
                Text("Baking…").foregroundStyle(.secondary)
            }
        default:
            Button {
                bake()
            } label: {
                Label("Bake to new sample", systemImage: "flame")
            }
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
    }

    private func bake() {
        bakeState = .baking
        Task {
            do {
                _ = try await appState.modeCoordinator.bakeTransforms(
                    gridPad: gridRaw
                )
                chain = appState.modeCoordinator.transformChain(
                    gridPad: gridRaw
                )
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
        push()
    }

    private func push() {
        appState.modeCoordinator.setTransformChain(chain, gridPad: gridRaw)
    }

    /// Every-other-eighth default pattern, and a guard that persisted
    /// step arrays are exactly 16 long before editing.
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
        case .stretch(let f):     return String(format: "Stretch %.2f×", f)
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
