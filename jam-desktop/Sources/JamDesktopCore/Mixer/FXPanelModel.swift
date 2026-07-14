// FXPanelModel.swift
//
// Intent model for the D-022 master FX panel (Levels|FX segment in
// the mixer): 3-band EQ, compressor, reverb/delay sends, FX return
// level and preset picker. Mirrors the mobile FXSettingsStore
// semantics on top of the desktop persistence split:
//
//   - every param edit autosaves and fires `onFXChanged` so the
//     audio side (MusicBus) stays in sync,
//   - a knob edit clears presetId to nil (displayed as "Custom"),
//   - applying a preset restores all params + presetId in one write.
//
// Audio-free (headless tests); SessionController wires onFXChanged
// to EngineController.musicBus.apply.

import Foundation
import Observation
import ToneForgeEngine

@Observable
@MainActor
public final class FXPanelModel {

    /// Fired after every committed settings change (knob edit, preset
    /// apply, reset) — NOT on initial load.
    public var onFXChanged: ((FXSettings) -> Void)?

    public private(set) var settings: FXSettings

    private let store: FXSettingsStore

    public init(store: FXSettingsStore = FXSettingsStore()) {
        self.store = store
        self.settings = store.load()
    }

    // MARK: - Derived accessors (knob edits clear the preset)

    public var eq: FXEQParams {
        get { settings.eq }
        set { edit { $0.eq = newValue } }
    }

    public var comp: FXCompParams {
        get { settings.comp }
        set { edit { $0.comp = newValue } }
    }

    public var reverb: FXReverbParams {
        get { settings.reverb }
        set { edit { $0.reverb = newValue } }
    }

    public var delay: FXDelayParams {
        get { settings.delay }
        set { edit { $0.delay = newValue } }
    }

    public var fxReturnDb: Double {
        get { settings.fxReturnDb }
        set { edit { $0.fxReturnDb = newValue } }
    }

    public var presetId: String? { settings.presetId }

    // MARK: - Presets

    /// Apply a preset from FXPresetCatalog: all params AND presetId
    /// land in one commit.
    public func applyPreset(_ preset: FXPreset) {
        commit(preset.settings)
    }

    /// Apply a preset by ID (nil or unknown ID → clean preset).
    public func applyPreset(id: String?) {
        applyPreset(id.flatMap { FXPresetCatalog.preset(id: $0) } ?? FXPresetCatalog.clean)
    }

    /// Back to neutral (everything off, no preset).
    public func reset() {
        commit(.neutral)
    }

    // MARK: - Private

    private func edit(_ mutate: (inout FXSettings) -> Void) {
        var s = settings
        mutate(&s)
        s.presetId = nil
        commit(s)
    }

    private func commit(_ newSettings: FXSettings) {
        settings = newSettings
        store.save(newSettings)
        onFXChanged?(newSettings)
    }
}
