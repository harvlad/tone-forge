// FXSettingsStore.swift
//
// UserDefaults-backed persistence for the D-022 master FX panel. Same
// single-blob pattern as SampleSettingsStore: one versioned JSON
// payload, `didSet` autosave on every @Published field, decodeIfPresent
// for fields added later, corrupt blobs silently replaced with defaults.
//
// The store wraps ToneForgeEngine.FXSettings and mirrors it to a
// @Published property. AppState sinks the published value and pushes
// it to AudioEngine.setFXSettings() so the audio graph stays in sync.
//
// Preset handling: when a preset is applied, presetId is set. Any
// subsequent knob edit clears presetId to nil (displayed as "Custom").

import Foundation
import ToneForgeEngine

@MainActor
public final class FXSettingsStore: ObservableObject {

    // MARK: - Published (auto-saved on change)

    /// The full FX settings (EQ, comp, reverb, delay, return, preset).
    @Published public var settings: FXSettings {
        didSet { save() }
    }

    // MARK: - Derived convenience accessors

    public var eq: FXEQParams {
        get { settings.eq }
        set {
            var s = settings
            s.eq = newValue
            s.presetId = nil  // knob edit clears preset
            settings = s
        }
    }

    public var comp: FXCompParams {
        get { settings.comp }
        set {
            var s = settings
            s.comp = newValue
            s.presetId = nil
            settings = s
        }
    }

    public var reverb: FXReverbParams {
        get { settings.reverb }
        set {
            var s = settings
            s.reverb = newValue
            s.presetId = nil
            settings = s
        }
    }

    public var delay: FXDelayParams {
        get { settings.delay }
        set {
            var s = settings
            s.delay = newValue
            s.presetId = nil
            settings = s
        }
    }

    public var fxReturnDb: Double {
        get { settings.fxReturnDb }
        set {
            var s = settings
            s.fxReturnDb = newValue
            s.presetId = nil
            settings = s
        }
    }

    public var presetId: String? { settings.presetId }

    /// Apply a preset from FXPresetCatalog. This sets ALL params AND
    /// presetId in one write, so the autosave captures the full state.
    public func applyPreset(_ preset: FXPreset) {
        settings = preset.settings
    }

    /// Apply a preset by ID (nil or unknown ID → clean preset).
    public func applyPreset(id: String?) {
        let preset = id.flatMap { FXPresetCatalog.preset(id: $0) } ?? FXPresetCatalog.clean
        applyPreset(preset)
    }

    // MARK: - Persistence

    private let defaults: UserDefaults
    private let key = "toneforge.fxSettings"

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.settings = Self.load(from: defaults, key: key)
    }

    private func save() {
        do {
            let data = try JSONEncoder().encode(settings)
            defaults.set(data, forKey: key)
        } catch {
            print("[FXSettingsStore] encode failed: \(error)")
        }
    }

    private static func load(from defaults: UserDefaults, key: String) -> FXSettings {
        guard let data = defaults.data(forKey: key) else {
            return .neutral
        }
        do {
            return try JSONDecoder().decode(FXSettings.self, from: data)
        } catch {
            print("[FXSettingsStore] decode failed, using neutral: \(error)")
            return .neutral
        }
    }
}
