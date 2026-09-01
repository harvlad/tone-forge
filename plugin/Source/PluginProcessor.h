// PluginProcessor.h — jamn Kit milestone 1.
//
// Plays a loaded kit pack: one voice per pad, MIDI C1 up (same map as
// the .adg export). Loopable pads sustain-loop while the note is held
// (gate); one-shots play through. With no pack loaded, pads fall back
// to the milestone-0 sine pings so the plugin is always audible.
//
// Threading: packs load on the message thread and swap in via a
// SpinLock-guarded shared_ptr; the audio thread try-locks and keeps
// using its current snapshot if the swap is mid-flight.

#pragma once

#include "KitPack.h"

#include <juce_dsp/juce_dsp.h>

class JamnKitProcessor : public juce::AudioProcessor
{
public:
    JamnKitProcessor();

    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override {}
    void processBlock(juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return "jamn Kit"; }
    bool acceptsMidi() const override { return true; }
    bool producesMidi() const override { return false; }
    double getTailLengthSeconds() const override { return 0.5; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram(int) override {}
    const juce::String getProgramName(int) override { return {}; }
    void changeProgramName(int, const juce::String&) override {}

    /// Persist the pack path so a saved DAW project reopens the kit.
    void getStateInformation(juce::MemoryBlock&) override;
    void setStateInformation(const void*, int) override;

    // MARK: Pack (message thread)

    /// Load a pack (folder or exporter zip). Returns error text, empty
    /// on success. Message thread only.
    juce::String loadPack(const juce::File& source);

    /// Durable kit storage (~/Library/Application Support/jamnKit/Kits
    /// on macOS). Downloads land here — never the OS temp dir — so a
    /// saved DAW project still finds its kit after temp cleanup.
    static juce::File kitStoreDir();

    /// Current pack snapshot for the editor (may be null).
    std::shared_ptr<const LoadedPack> currentPack() const;

    /// Editor pad state: is the voice for `midiNote` sounding?
    bool isNoteActive(int midiNote) const;

    /// Editor pad state: queued for the next host bar (launch quantize)?
    bool isNoteArmed(int midiNote) const;

    /// UI-triggered pad press/release (editor pads mirror MIDI).
    void noteOnFromUI(int midiNote);
    void noteOffFromUI(int midiNote);

    /// Per-pad loop division in HOST BARS (0 = full sample, else the
    /// loop wraps every N bars so it stays locked to the DAW grid).
    /// Right-click on a pad cycles Full -> 8 -> 4 -> 2 -> 1.
    int padDivision(int slot) const
    {
        return slot >= 0 && slot < kVoices
            ? padDivisions[(size_t) slot].load() : 0;
    }
    void cyclePadDivision(int slot)
    {
        if (slot < 0 || slot >= kVoices)
            return;
        auto& d = padDivisions[(size_t) slot];
        switch (d.load())
        {
            case 0: d.store(8); break;
            case 8: d.store(4); break;
            case 4: d.store(2); break;
            case 2: d.store(1); break;
            default:
                d.store(0);
                padStarts[(size_t) slot].store(0);  // Full = whole sample
                break;
        }
    }
    /// Loop-region START offset in host bars (0 = sample start).
    int padStart(int slot) const
    {
        return slot >= 0 && slot < kVoices
            ? padStarts[(size_t) slot].load() : 0;
    }

    /// Drag-trim: set the loop REGION [startBars, startBars+lengthBars]
    /// (length 0 = to the end). Applies LIVE — the render reads it
    /// every block, so a looping pad re-wraps as you drag either edge.
    void setPadRegion(int slot, int startBars, int lengthBars)
    {
        if (slot < 0 || slot >= kVoices)
            return;
        padStarts[(size_t) slot].store(juce::jmax(0, startBars));
        padDivisions[(size_t) slot].store(juce::jmax(0, lengthBars));
    }

    /// Host tempo snapshot for the editor's trim math.
    double hostBpm() const { return lastBpm; }
    double hostBarBeats() const { return lastBarPpq; }

    struct HostClock
    {
        double bpm = 0.0;
        double ppqPosition = 0.0;
        double barPhase = 0.0;  // 0..1 within the current bar
        bool playing = false;
    };
    HostClock hostClock() const { return clock.load(); }

    /// 0..1 loop phase of a playing pad slot, or -1 when silent —
    /// drives the editor's pad playheads.
    float padPhase(int slot) const
    {
        return slot >= 0 && slot < kVoices
            ? padPhases[(size_t) slot].load() : -1.0f;
    }

    /// Macro parameters (Filter / Space / Drive / Gain) — automatable.
    juce::AudioProcessorValueTreeState apvts;

    /// Backend base URL for the in-plugin pack browser (persisted).
    juce::String backendUrl() const { return backendUrlValue; }
    void setBackendUrl(const juce::String& url) { backendUrlValue = url; }

    /// Feedback loop: drain queued pad events ({assetId, "play"|"skip"})
    /// — the editor batches these to POST /pad-feedback. Message thread.
    std::vector<std::pair<juce::String, juce::String>> drainFeedback()
    {
        const juce::SpinLock::ScopedLockType lock(feedbackLock);
        auto out = std::move(pendingFeedback);
        pendingFeedback.clear();
        return out;
    }

    static constexpr int kVoices = 16;
    static constexpr int kFirstNote = 36;  // C1

private:
    struct Voice
    {
        enum class State { idle, armed, playing, releasing };
        State state = State::idle;
        /// Armed while the transport is STOPPED (Arm & Wait): holds
        /// until the host starts rolling, then launches on the first
        /// bar together with every other waiting pad.
        bool waitForTransport = false;
        // Sample playback (pack mode)
        const KitPadSample* pad = nullptr;  // borrowed from activePack
        double position = 0.0;
        double step = 1.0;
        bool held = false;
        /// Samples until the armed voice fires (cycle quantize).
        double startDelaySamples = 0.0;
        /// Declick ramp counter after a division wrap (the trim point
        /// has no baked crossfade seam).
        int wrapRamp = 0;
        /// Gate-off fade (avoids the hard-cut click).
        float releaseGain = 1.0f;
        float releaseStep = 0.0f;
        /// Sample-clock when the voice became audible (feedback loop).
        juce::int64 startClock = 0;
        // Sine fallback (no pack)
        double phase = 0.0;
        double increment = 0.0;
        float sineLevel = 0.0f;
    };

    /// `eventPpq` = host ppq at the event's sample offset; < 0 = host
    /// clock unusable (fire immediately).
    void handleNoteOn(int note, float velocity, double eventPpq,
                      double samplesPerPpq, double barPpq);
    double sharedCyclePpq(double barPpq) const;
    void handleNoteOff(int note);
    void renderVoice(Voice& v, int slot, float* left, float* right,
                     int numSamples);

    static juce::AudioProcessorValueTreeState::ParameterLayout
        parameterLayout();
    void applyMacros(juce::AudioBuffer<float>&);

    std::array<Voice, kVoices> voices {};
    std::array<std::atomic<bool>, 128> activeNotes {};
    std::array<std::atomic<bool>, 128> armedNotes {};
    std::array<std::atomic<float>, kVoices> padPhases {};
    std::array<std::atomic<int>, kVoices> padDivisions {};
    std::array<std::atomic<int>, kVoices> padStarts {};
    /// Host clock cached for noteOn-time trim math.
    double lastBpm = 0.0, lastBarPpq = 4.0;
    /// Loaded pack's song tempo (0 = unknown) — drives rate-matching.
    std::atomic<double> packTempoBpm { 0.0 };

    // Macro DSP: soft drive -> lowpass -> reverb wet -> master gain.
    juce::dsp::StateVariableTPTFilter<float> lowpass;
    juce::Reverb reverb;
    std::atomic<float>* pFilter = nullptr;
    std::atomic<float>* pSpace = nullptr;
    std::atomic<float>* pDrive = nullptr;
    std::atomic<float>* pGain = nullptr;
    std::atomic<float>* pArm = nullptr;
    std::atomic<float>* pLearn = nullptr;
    bool wasPlaying = false;

    /// Feedback loop plumbing. Push is audio-thread (event-rate only,
    /// try-lock, capped); drain is message-thread.
    void pushFeedback(const juce::String& assetId, const char* kind);
    juce::SpinLock feedbackLock;
    std::vector<std::pair<juce::String, juce::String>> pendingFeedback;
    juce::int64 sampleClock = 0;

    juce::String backendUrlValue { "http://127.0.0.1:8300" };
    double currentSampleRate = 44100.0;

    juce::SpinLock packLock;
    std::shared_ptr<const LoadedPack> activePack;      // audio-thread view
    std::shared_ptr<const LoadedPack> editorPack;      // message-thread view
    // MIDI events synthesized from UI presses, merged into processBlock.
    juce::MidiMessageCollector uiMidi;

    std::atomic<HostClock> clock { HostClock{} };

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(JamnKitProcessor)
};
