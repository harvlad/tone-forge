// PluginProcessor.cpp — see PluginProcessor.h.
//
// Milestone 2: loopable pads LAUNCH-QUANTIZE to the host's next bar
// while the transport rolls (sample-accurate, computed from ppq), gate
// off with a 20 ms release fade, and loop over a baked crossfade seam.
// One-shots stay immediate — drum-machine feel.

#include "PluginProcessor.h"
#include "PluginEditor.h"

#include <cmath>

JamnKitProcessor::JamnKitProcessor()
    : AudioProcessor(BusesProperties().withOutput(
          "Output", juce::AudioChannelSet::stereo(), true))
{
    for (auto& n : activeNotes)
        n.store(false);
    for (auto& n : armedNotes)
        n.store(false);
}

void JamnKitProcessor::prepareToPlay(double sampleRate, int)
{
    currentSampleRate = sampleRate;
    voices.fill({});
    uiMidi.reset(sampleRate);
    for (auto& n : activeNotes)
        n.store(false);
    for (auto& n : armedNotes)
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
            v = {};
        for (auto& n : activeNotes)
            n.store(false);
        for (auto& n : armedNotes)
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

bool JamnKitProcessor::isNoteArmed(int midiNote) const
{
    return midiNote >= 0 && midiNote < 128
        && armedNotes[(size_t) midiNote].load();
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

void JamnKitProcessor::handleNoteOn(int note, float velocity,
                                    double eventPpq, double samplesPerPpq,
                                    double barPpq)
{
    const int slot = note - kFirstNote;
    if (slot < 0 || slot >= kVoices)
        return;
    auto& v = voices[(size_t) slot];

    const KitPadSample* pad =
        activePack != nullptr ? activePack->padForNote(note) : nullptr;

    if (pad != nullptr && pad->loopable)
    {
        // TOGGLE launch (clip-launcher semantics). Gate + quantize was
        // unusable: a mouse click / short MIDI note sent note-off
        // before the bar arrived and the arm self-cancelled — "arming
        // not happening". Tap = arm for the next bar; tap while armed
        // = cancel; tap while looping = release.
        if (v.state == Voice::State::armed)
        {
            v = {};
            armedNotes[(size_t) note].store(false);
            return;
        }
        if (v.state == Voice::State::playing)
        {
            v.state = Voice::State::releasing;
            v.releaseGain = 1.0f;
            v.releaseStep = 1.0f
                / juce::jmax(1.0f, 0.020f * (float) currentSampleRate);
            return;
        }

        v = {};
        v.pad = pad;
        v.position = 0.0;
        v.step = pad->sourceSampleRate / currentSampleRate;
        if (eventPpq >= 0.0 && samplesPerPpq > 0.0)
        {
            double intoBar = std::fmod(eventPpq, barPpq);
            if (intoBar < 0.0)
                intoBar += barPpq;  // pre-roll / count-in ppq is negative
            const double grace = 1.0 / 32.0;
            const double toNext =
                (intoBar < grace) ? 0.0 : (barPpq - intoBar);
            if (toNext > 0.0)
            {
                v.state = Voice::State::armed;
                v.startDelaySamples = toNext * samplesPerPpq;
                armedNotes[(size_t) note].store(true);
                return;
            }
        }
        v.state = Voice::State::playing;
        activeNotes[(size_t) note].store(true);
        return;
    }

    v = {};
    v.held = true;

    if (pad != nullptr)
    {
        // One-shot: immediate, plays through (drum-machine feel).
        v.pad = pad;
        v.position = 0.0;
        v.step = pad->sourceSampleRate / currentSampleRate;
        v.state = Voice::State::playing;
        activeNotes[(size_t) note].store(true);
        return;
    }

    // Sine fallback (no pack / unmapped note-in-range).
    const double hz = 110.0 * std::pow(2.0, slot / 5.0);
    v.phase = 0.0;
    v.increment = juce::MathConstants<double>::twoPi * hz / currentSampleRate;
    v.sineLevel = velocity * 0.4f;
    v.state = Voice::State::playing;
    activeNotes[(size_t) note].store(true);
}

void JamnKitProcessor::handleNoteOff(int note)
{
    const int slot = note - kFirstNote;
    if (slot < 0 || slot >= kVoices)
        return;
    auto& v = voices[(size_t) slot];
    v.held = false;
    // Note-offs are IGNORED for loopable pads (toggle semantics — the
    // next note-on releases); one-shots play through regardless.
}

void JamnKitProcessor::renderVoice(Voice& v, int slot, float* left,
                                   float* right, int numSamples)
{
    const int note = slot + kFirstNote;
    int start = 0;

    if (v.state == Voice::State::armed)
    {
        if (v.startDelaySamples >= (double) numSamples)
        {
            v.startDelaySamples -= numSamples;
            return;
        }
        start = (int) v.startDelaySamples;
        v.startDelaySamples = 0.0;
        v.state = Voice::State::playing;
        armedNotes[(size_t) note].store(false);
        activeNotes[(size_t) note].store(true);
    }

    if (v.pad != nullptr)
    {
        const auto& audio = v.pad->audio;
        const int length = audio.getNumSamples();
        const int channels = audio.getNumChannels();
        if (length < 2)
        {
            v = {};
            activeNotes[(size_t) note].store(false);
            return;
        }
        const float* srcL = audio.getReadPointer(0);
        const float* srcR = channels > 1 ? audio.getReadPointer(1) : srcL;

        for (int i = start; i < numSamples; ++i)
        {
            if (v.position >= length - 1)
            {
                if (v.pad->loopable)
                    v.position = 0.0;  // seam is baked; keeps wrapping
                                       // through the release fade too
                else
                {
                    v = {};
                    activeNotes[(size_t) note].store(false);
                    return;
                }
            }
            const int idx = (int) v.position;
            const float frac = (float) (v.position - idx);
            float gain = 1.0f;
            if (v.state == Voice::State::releasing)
            {
                v.releaseGain -= v.releaseStep;
                if (v.releaseGain <= 0.0f)
                {
                    v = {};
                    activeNotes[(size_t) note].store(false);
                    return;
                }
                gain = v.releaseGain;
            }
            left[i] += gain * (srcL[idx] + frac * (srcL[idx + 1] - srcL[idx]));
            if (right != left)
                right[i] +=
                    gain * (srcR[idx] + frac * (srcR[idx + 1] - srcR[idx]));
            v.position += v.step;
        }
        return;
    }

    // Sine fallback.
    const float sineDecay =
        std::exp(-1.0f / (0.25f * (float) currentSampleRate));
    for (int i = start; i < numSamples; ++i)
    {
        if (v.sineLevel <= 0.0005f)
        {
            v = {};
            activeNotes[(size_t) note].store(false);
            return;
        }
        const float s = v.sineLevel * (float) std::sin(v.phase);
        left[i] += s;
        if (right != left)
            right[i] += s;
        v.phase += v.increment;
        v.sineLevel *= sineDecay;
    }
}

void JamnKitProcessor::processBlock(
    juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    double blockPpq = -1.0, samplesPerPpq = 0.0, barPpq = 4.0;
    bool hostPlaying = false;
    if (auto* playHead = getPlayHead())
    {
        if (auto position = playHead->getPosition())
        {
            HostClock c;
            c.bpm = position->getBpm().orFallback(0.0);
            c.ppqPosition = position->getPpqPosition().orFallback(0.0);
            c.playing = position->getIsPlaying();
            clock.store(c);
            hostPlaying = c.playing;
            if (c.bpm > 0.0)
            {
                samplesPerPpq = currentSampleRate * 60.0 / c.bpm;
                blockPpq = c.ppqPosition;
            }
            if (auto sig = position->getTimeSignature())
                barPpq = juce::jmax(
                    1.0, 4.0 * sig->numerator / juce::jmax(1, sig->denominator));
        }
    }
    // Quantize only makes sense on a rolling transport.
    const bool quantizable = hostPlaying && blockPpq >= 0.0
        && samplesPerPpq > 0.0;

    uiMidi.removeNextBlockOfMessages(midi, buffer.getNumSamples());

    const juce::SpinLock::ScopedTryLockType lock(packLock);
    if (!lock.isLocked())
        return;

    for (const auto metadata : midi)
    {
        const auto msg = metadata.getMessage();
        if (msg.isNoteOn())
        {
            const double eventPpq = quantizable
                ? blockPpq + metadata.samplePosition / samplesPerPpq
                : -1.0;
            handleNoteOn(msg.getNoteNumber(), msg.getFloatVelocity(),
                         eventPpq, samplesPerPpq, barPpq);
        }
        else if (msg.isNoteOff())
            handleNoteOff(msg.getNoteNumber());
    }

    const int numSamples = buffer.getNumSamples();
    auto* left = buffer.getWritePointer(0);
    auto* right = buffer.getNumChannels() > 1 ? buffer.getWritePointer(1) : left;

    for (int slot = 0; slot < kVoices; ++slot)
    {
        auto& v = voices[(size_t) slot];
        if (v.state != Voice::State::idle)
            renderVoice(v, slot, left, right, numSamples);
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
