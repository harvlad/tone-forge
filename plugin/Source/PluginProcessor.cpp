// PluginProcessor.cpp — see PluginProcessor.h.

#include "PluginProcessor.h"
#include "PluginEditor.h"

#include <cmath>

JamnKitProcessor::JamnKitProcessor()
    : AudioProcessor(BusesProperties().withOutput(
          "Output", juce::AudioChannelSet::stereo(), true))
{
    for (auto& n : activeNotes)
        n.store(false);
}

void JamnKitProcessor::prepareToPlay(double sampleRate, int)
{
    currentSampleRate = sampleRate;
    voices.fill({});
    uiMidi.reset(sampleRate);
    for (auto& n : activeNotes)
        n.store(false);
}

// MARK: - Pack management (message thread)

juce::String JamnKitProcessor::loadPack(const juce::File& source)
{
    juce::String error;
    auto pack = kitpack::load(source, error);
    if (pack == nullptr)
        return error.isNotEmpty() ? error : juce::String("Pack failed to load.");

    {
        const juce::SpinLock::ScopedLockType lock(packLock);
        activePack = pack;
        for (auto& v : voices)
            v = {};  // old pad pointers die with the old pack
        for (auto& n : activeNotes)
            n.store(false);
    }
    editorPack = pack;
    return {};
}

std::shared_ptr<const LoadedPack> JamnKitProcessor::currentPack() const
{
    return editorPack;
}

bool JamnKitProcessor::isNoteActive(int midiNote) const
{
    return midiNote >= 0 && midiNote < 128
        && activeNotes[(size_t) midiNote].load();
}

void JamnKitProcessor::noteOnFromUI(int midiNote)
{
    uiMidi.addMessageToQueue(juce::MidiMessage::noteOn(1, midiNote, 1.0f)
                                 .withTimeStamp(juce::Time::getMillisecondCounterHiRes() * 0.001));
}

void JamnKitProcessor::noteOffFromUI(int midiNote)
{
    uiMidi.addMessageToQueue(juce::MidiMessage::noteOff(1, midiNote)
                                 .withTimeStamp(juce::Time::getMillisecondCounterHiRes() * 0.001));
}

// MARK: - Voices (audio thread; caller holds packLock via processBlock)

void JamnKitProcessor::handleNoteOn(int note, float velocity)
{
    const int slot = note - kFirstNote;
    if (slot < 0 || slot >= kVoices)
        return;
    auto& v = voices[(size_t) slot];
    v = {};
    v.held = true;

    if (activePack != nullptr)
    {
        if (const auto* pad = activePack->padForNote(note))
        {
            v.pad = pad;
            v.position = 0.0;
            v.step = pad->sourceSampleRate / currentSampleRate;
            v.active = true;
            activeNotes[(size_t) note].store(true);
            return;
        }
    }
    // Sine fallback (no pack / unmapped note-in-range).
    const double hz = 110.0 * std::pow(2.0, slot / 5.0);
    v.phase = 0.0;
    v.increment = juce::MathConstants<double>::twoPi * hz / currentSampleRate;
    v.sineLevel = velocity * 0.4f;
    v.active = true;
    activeNotes[(size_t) note].store(true);
}

void JamnKitProcessor::handleNoteOff(int note)
{
    const int slot = note - kFirstNote;
    if (slot < 0 || slot >= kVoices)
        return;
    auto& v = voices[(size_t) slot];
    v.held = false;
    // Loopable pads gate off with the note; one-shots play through.
    if (v.pad != nullptr && v.pad->loopable)
    {
        v.active = false;
        v.pad = nullptr;
        activeNotes[(size_t) note].store(false);
    }
}

void JamnKitProcessor::processBlock(
    juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

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

    // Merge UI pad presses with host MIDI.
    uiMidi.removeNextBlockOfMessages(midi, buffer.getNumSamples());

    const juce::SpinLock::ScopedTryLockType lock(packLock);
    if (!lock.isLocked())
        return;  // pack swap in flight — one silent block is fine

    for (const auto metadata : midi)
    {
        const auto msg = metadata.getMessage();
        if (msg.isNoteOn())
            handleNoteOn(msg.getNoteNumber(), msg.getFloatVelocity());
        else if (msg.isNoteOff())
            handleNoteOff(msg.getNoteNumber());
    }

    const int numSamples = buffer.getNumSamples();
    auto* left = buffer.getWritePointer(0);
    auto* right = buffer.getNumChannels() > 1 ? buffer.getWritePointer(1) : left;
    const float sineDecay =
        std::exp(-1.0f / (0.25f * (float) currentSampleRate));

    for (int slot = 0; slot < kVoices; ++slot)
    {
        auto& v = voices[(size_t) slot];
        if (!v.active)
            continue;

        if (v.pad != nullptr)
        {
            const auto& audio = v.pad->audio;
            const int length = audio.getNumSamples();
            const int channels = audio.getNumChannels();
            if (length < 2)
            {
                v.active = false;
                continue;
            }
            const float* srcL = audio.getReadPointer(0);
            const float* srcR =
                channels > 1 ? audio.getReadPointer(1) : srcL;

            for (int i = 0; i < numSamples; ++i)
            {
                if (v.position >= length - 1)
                {
                    if (v.pad->loopable && v.held)
                        v.position = 0.0;
                    else
                    {
                        v.active = false;
                        v.pad = nullptr;
                        activeNotes[(size_t)(slot + kFirstNote)].store(false);
                        break;
                    }
                }
                const int idx = (int) v.position;
                const float frac = (float) (v.position - idx);
                left[i] += srcL[idx] + frac * (srcL[idx + 1] - srcL[idx]);
                if (right != left)
                    right[i] += srcR[idx] + frac * (srcR[idx + 1] - srcR[idx]);
                v.position += v.step;
            }
        }
        else
        {
            for (int i = 0; i < numSamples; ++i)
            {
                if (v.sineLevel <= 0.0005f)
                {
                    v.active = false;
                    activeNotes[(size_t)(slot + kFirstNote)].store(false);
                    break;
                }
                const float s = v.sineLevel * (float) std::sin(v.phase);
                left[i] += s;
                if (right != left)
                    right[i] += s;
                v.phase += v.increment;
                v.sineLevel *= sineDecay;
            }
        }
    }
}

// MARK: - State

void JamnKitProcessor::getStateInformation(juce::MemoryBlock& dest)
{
    juce::DynamicObject::Ptr obj = new juce::DynamicObject();
    obj->setProperty("packPath",
                     editorPack != nullptr ? editorPack->sourcePath
                                           : juce::String());
    const auto json = juce::JSON::toString(juce::var(obj.get()));
    dest.replaceAll(json.toRawUTF8(), json.getNumBytesAsUTF8());
}

void JamnKitProcessor::setStateInformation(const void* data, int size)
{
    const auto parsed = juce::JSON::parse(
        juce::String::fromUTF8((const char*) data, size));
    const juce::String path =
        parsed.getProperty("packPath", juce::String()).toString();
    if (path.isEmpty())
        return;
    const juce::File source(path);
    if (source.exists())
        (void) loadPack(source);
}

juce::AudioProcessorEditor* JamnKitProcessor::createEditor()
{
    return new JamnKitEditor(*this);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new JamnKitProcessor();
}
