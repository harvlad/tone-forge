// PluginProcessor.h — jamn Kit milestone 0.
//
// Audio side of the skeleton: a 16-voice sine "ping" per MIDI note so
// loading the plugin is immediately audible/testable from a pad
// controller or piano roll. Milestone 1 swaps the sine voices for the
// kit-pack sample player (same voice slots, same C1..D#2 map).

#pragma once

#include <juce_audio_utils/juce_audio_utils.h>

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

    void getStateInformation(juce::MemoryBlock&) override {}
    void setStateInformation(const void*, int) override {}

    /// Host transport snapshot for the editor readout (and, from
    /// milestone 2, the launch-quantize grid).
    struct HostClock
    {
        double bpm = 0.0;
        double ppqPosition = 0.0;
        bool playing = false;
    };
    HostClock hostClock() const { return clock.load(); }

private:
    struct Voice
    {
        double phase = 0.0;
        double increment = 0.0;
        float level = 0.0f;   // decaying envelope; 0 = free
    };
    static constexpr int kVoices = 16;
    static constexpr int kFirstNote = 36;  // C1, same map as the .adg

    std::array<Voice, kVoices> voices {};
    double currentSampleRate = 44100.0;

    std::atomic<HostClock> clock { HostClock{} };

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(JamnKitProcessor)
};
