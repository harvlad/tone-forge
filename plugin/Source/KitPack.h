// KitPack.h — jamn Kit pack model + loader (milestone 1).
//
// A "pack" is exactly what the backend's Ableton exporter emits:
//   {Song} Jam Kit/
//     kit.json      — with a `samples` array (MIDI-ordered render list)
//     Samples/*.wav — the stem slices
// Accepted as the folder or the original zip (extracted to a temp dir).
//
// Loading is MESSAGE-THREAD ONLY (file I/O + decode). The processor
// swaps the finished LoadedPack in atomically; the audio thread only
// ever reads an immutable snapshot.

#pragma once

#include <juce_audio_utils/juce_audio_utils.h>

struct KitPadSample
{
    juce::String name;
    juce::String category;   // DRUMS/BASS/CHORDS/... or empty
    /// Stable graph-asset id — the feedback loop keys play/skip
    /// events on this (empty on legacy packs: no feedback sent).
    juce::String assetId;
    int midiNote = 36;
    bool loopable = true;
    double sourceSampleRate = 44100.0;
    juce::AudioBuffer<float> audio;  // decoded at source rate
    /// 64-bin normalized peak envelope for the pad's mini waveform.
    std::vector<float> peaks;

    juce::Colour colour() const;
};

struct LoadedPack
{
    juce::String songName;
    /// Backend analysis id — target for pad-feedback posts.
    juce::String entryId;
    double tempoBpm = 0.0;
    juce::String sourcePath;            // what to persist/restore
    std::vector<KitPadSample> pads;     // MIDI-ordered (note 36 up)

    /// Pad for a MIDI note, or nullptr.
    const KitPadSample* padForNote(int note) const
    {
        for (auto& p : pads)
            if (p.midiNote == note)
                return &p;
        return nullptr;
    }
};

namespace kitpack
{
/// Load a pack from a folder or a .zip (exporter output). Returns
/// nullptr with `error` set on failure. Message thread only.
std::shared_ptr<const LoadedPack> load(const juce::File& source,
                                       juce::String& error);
}  // namespace kitpack
