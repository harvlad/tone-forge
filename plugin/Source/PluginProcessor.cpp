// PluginProcessor.cpp — see PluginProcessor.h.

#include "PluginProcessor.h"
#include "PluginEditor.h"

#include <cmath>

JamnKitProcessor::JamnKitProcessor()
    : AudioProcessor(BusesProperties().withOutput(
          "Output", juce::AudioChannelSet::stereo(), true))
{
}

void JamnKitProcessor::prepareToPlay(double sampleRate, int)
{
    currentSampleRate = sampleRate;
    voices.fill({});
}

void JamnKitProcessor::processBlock(
    juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    // Host clock snapshot for the editor (milestone 2 quantizes on it).
    if (auto* playHead = getPlayHead())
    {
        if (auto position = playHead->getPosition())
        {
            HostClock c;
            c.bpm = position->getBpm().orFallback(0.0);
            c.ppqPosition = position->getPpqPosition().orFallback(0.0);
            c.playing = position->getIsPlaying();
            clock.store(c);
        }
    }

    for (const auto metadata : midi)
    {
        const auto msg = metadata.getMessage();
        if (!msg.isNoteOn())
            continue;
        const int pad = msg.getNoteNumber() - kFirstNote;
        if (pad < 0 || pad >= kVoices)
            continue;
        auto& v = voices[(size_t) pad];
        // Audible per-pad pitch: A minor pentatonic-ish ladder from A2.
        const double hz = 110.0 * std::pow(2.0, pad / 5.0);
        v.phase = 0.0;
        v.increment = juce::MathConstants<double>::twoPi * hz / currentSampleRate;
        v.level = msg.getFloatVelocity() * 0.4f;
    }

    const int numSamples = buffer.getNumSamples();
    auto* left = buffer.getWritePointer(0);
    auto* right = buffer.getNumChannels() > 1 ? buffer.getWritePointer(1) : left;
    const float decay = std::exp(-1.0f / (0.25f * (float) currentSampleRate));

    for (int i = 0; i < numSamples; ++i)
    {
        float sample = 0.0f;
        for (auto& v : voices)
        {
            if (v.level <= 0.0005f)
                continue;
            sample += v.level * (float) std::sin(v.phase);
            v.phase += v.increment;
            v.level *= decay;
        }
        left[i] += sample;
        if (right != left)
            right[i] += sample;
    }
}

juce::AudioProcessorEditor* JamnKitProcessor::createEditor()
{
    return new JamnKitEditor(*this);
}

// JUCE plugin entry point.
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new JamnKitProcessor();
}
