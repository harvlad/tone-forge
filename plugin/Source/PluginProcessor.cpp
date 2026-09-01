// PluginProcessor.cpp — see PluginProcessor.h.
//
// Milestone 2: loopable pads LAUNCH-QUANTIZE to the host's next bar
// while the transport rolls (sample-accurate, computed from ppq), gate
// off with a 20 ms release fade, and loop over a baked crossfade seam.
// One-shots stay immediate — drum-machine feel.

#include "PluginProcessor.h"
#include "PluginEditor.h"

#include <cmath>

juce::AudioProcessorValueTreeState::ParameterLayout
JamnKitProcessor::parameterLayout()
{
    using P = juce::AudioParameterFloat;
    // Display formatting lives on the PARAMETERS — SliderAttachment
    // overwrites any slider-level textFromValueFunction with these.
    const auto pct = juce::AudioParameterFloatAttributes()
        .withStringFromValueFunction([](float v, int) {
            return juce::String(juce::roundToInt(v * 100.0f)) + " %";
        });
    const auto db = juce::AudioParameterFloatAttributes()
        .withStringFromValueFunction([](float v, int) {
            return juce::String(v, 1) + " dB";
        });
    juce::AudioProcessorValueTreeState::ParameterLayout layout;
    layout.add(std::make_unique<P>("filter", "Filter",
                                   juce::NormalisableRange<float>(0.f, 1.f),
                                   1.0f, pct));
    layout.add(std::make_unique<P>("space", "Space",
                                   juce::NormalisableRange<float>(0.f, 1.f),
                                   0.0f, pct));
    layout.add(std::make_unique<P>("drive", "Drive",
                                   juce::NormalisableRange<float>(0.f, 1.f),
                                   0.0f, pct));
    layout.add(std::make_unique<P>(
        "gain", "Gain",
        juce::NormalisableRange<float>(-24.0f, 6.0f, 0.1f), 0.0f, db));
    // Arm & Wait: with the transport stopped, loop taps QUEUE (amber)
    // and all launch together on the first bar when the host starts.
    layout.add(std::make_unique<juce::AudioParameterBool>(
        "arm", "Arm & Wait", true));
    return layout;
}

JamnKitProcessor::JamnKitProcessor()
    : AudioProcessor(BusesProperties().withOutput(
          "Output", juce::AudioChannelSet::stereo(), true)),
      apvts(*this, nullptr, "params", parameterLayout())
{
    for (auto& n : activeNotes)
        n.store(false);
    for (auto& n : armedNotes)
        n.store(false);
    for (auto& p : padPhases)
        p.store(-1.0f);
    pFilter = apvts.getRawParameterValue("filter");
    pSpace = apvts.getRawParameterValue("space");
    pDrive = apvts.getRawParameterValue("drive");
    pGain = apvts.getRawParameterValue("gain");
    pArm = apvts.getRawParameterValue("arm");
}

void JamnKitProcessor::prepareToPlay(double sampleRate, int samplesPerBlock)
{
    currentSampleRate = sampleRate;
    voices.fill({});
    uiMidi.reset(sampleRate);
    for (auto& n : activeNotes)
        n.store(false);
    for (auto& n : armedNotes)
        n.store(false);
    for (auto& p : padPhases)
        p.store(-1.0f);

    juce::dsp::ProcessSpec spec { sampleRate,
                                  (juce::uint32) samplesPerBlock, 2 };
    lowpass.prepare(spec);
    lowpass.setType(juce::dsp::StateVariableTPTFilterType::lowpass);
    reverb.setSampleRate(sampleRate);
}

void JamnKitProcessor::applyMacros(juce::AudioBuffer<float>& buffer)
{
    const float drive = pDrive != nullptr ? pDrive->load() : 0.0f;
    const float filterAmt = pFilter != nullptr ? pFilter->load() : 1.0f;
    const float space = pSpace != nullptr ? pSpace->load() : 0.0f;
    const float gainDb = pGain != nullptr ? pGain->load() : 0.0f;
    const int numSamples = buffer.getNumSamples();

    if (drive > 0.001f)
    {
        const float pre = 1.0f + drive * 8.0f;
        const float post = 1.0f / std::sqrt(pre);
        for (int ch = 0; ch < buffer.getNumChannels(); ++ch)
        {
            auto* d = buffer.getWritePointer(ch);
            for (int i = 0; i < numSamples; ++i)
                d[i] = std::tanh(d[i] * pre) * post;
        }
    }

    if (filterAmt < 0.999f)
    {
        // 1.0 = wide open; sweep down to ~200 Hz, log-ish.
        const float cutoff =
            200.0f * std::pow(100.0f, juce::jlimit(0.0f, 1.0f, filterAmt));
        lowpass.setCutoffFrequency(
            juce::jmin(cutoff, (float) currentSampleRate * 0.45f));
        juce::dsp::AudioBlock<float> block(buffer);
        juce::dsp::ProcessContextReplacing<float> ctx(block);
        lowpass.process(ctx);
    }

    if (space > 0.001f)
    {
        juce::Reverb::Parameters rp;
        rp.roomSize = 0.6f;
        rp.damping = 0.4f;
        rp.wetLevel = space * 0.6f;
        rp.dryLevel = 1.0f - space * 0.3f;
        rp.width = 1.0f;
        reverb.setParameters(rp);
        if (buffer.getNumChannels() > 1)
            reverb.processStereo(buffer.getWritePointer(0),
                                 buffer.getWritePointer(1), numSamples);
        else
            reverb.processMono(buffer.getWritePointer(0), numSamples);
    }

    if (std::abs(gainDb) > 0.05f)
        buffer.applyGain(juce::Decibels::decibelsToGain(gainDb));
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
        else if (pArm != nullptr && pArm->load() > 0.5f)
        {
            // Arm & Wait: transport stopped — queue this pad; every
            // waiting pad launches together when the host starts.
            v.state = Voice::State::armed;
            v.waitForTransport = true;
            armedNotes[(size_t) note].store(true);
            return;
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
        if (v.waitForTransport)
            return;  // holds until the host transport starts
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
            if (auto sig = position->getTimeSignature())
                barPpq = juce::jmax(
                    1.0, 4.0 * sig->numerator / juce::jmax(1, sig->denominator));
            HostClock c;
            c.bpm = position->getBpm().orFallback(0.0);
            c.ppqPosition = position->getPpqPosition().orFallback(0.0);
            c.playing = position->getIsPlaying();
            double phase = std::fmod(c.ppqPosition, barPpq) / barPpq;
            if (phase < 0.0)
                phase += 1.0;
            c.barPhase = phase;
            clock.store(c);
            hostPlaying = c.playing;
            if (c.bpm > 0.0)
            {
                samplesPerPpq = currentSampleRate * 60.0 / c.bpm;
                blockPpq = c.ppqPosition;
            }
        }
    }
    // Quantize only makes sense on a rolling transport.
    const bool quantizable = hostPlaying && blockPpq >= 0.0
        && samplesPerPpq > 0.0;

    // Transport just started: release every Arm & Wait pad onto the
    // grid — they land together on the first bar boundary (usually
    // beat 1 itself, so the whole selection starts as one).
    if (hostPlaying && !wasPlaying)
    {
        for (auto& v : voices)
        {
            if (v.state != Voice::State::armed || !v.waitForTransport)
                continue;
            v.waitForTransport = false;
            double delay = 0.0;
            if (quantizable)
            {
                double intoBar = std::fmod(blockPpq, barPpq);
                if (intoBar < 0.0)
                    intoBar += barPpq;
                const double grace = 1.0 / 32.0;
                if (intoBar >= grace)
                    delay = (barPpq - intoBar) * samplesPerPpq;
            }
            v.startDelaySamples = delay;
        }
    }
    wasPlaying = hostPlaying;

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
        // Editor playhead: loop phase while sounding, -1 when silent.
        const bool sounding =
            (v.state == Voice::State::playing
             || v.state == Voice::State::releasing)
            && v.pad != nullptr && v.pad->audio.getNumSamples() > 0;
        padPhases[(size_t) slot].store(
            sounding ? (float) (v.position / v.pad->audio.getNumSamples())
                     : -1.0f);
    }

    applyMacros(buffer);
}

// MARK: - State

void JamnKitProcessor::getStateInformation(juce::MemoryBlock& dest)
{
    auto state = apvts.copyState();
    state.setProperty("packPath",
                      editorPack != nullptr ? editorPack->sourcePath
                                            : juce::String(),
                      nullptr);
    state.setProperty("backendUrl", backendUrlValue, nullptr);
    if (auto xml = state.createXml())
        copyXmlToBinary(*xml, dest);
}

void JamnKitProcessor::setStateInformation(const void* data, int size)
{
    auto xml = getXmlFromBinary(data, size);
    if (xml == nullptr)
        return;
    auto state = juce::ValueTree::fromXml(*xml);
    if (!state.isValid())
        return;
    apvts.replaceState(state);
    const juce::String url = state.getProperty("backendUrl", juce::String());
    if (url.isNotEmpty())
        backendUrlValue = url;
    const juce::String path = state.getProperty("packPath", juce::String());
    if (path.isNotEmpty())
    {
        const juce::File source(path);
        if (source.exists())
            (void) loadPack(source);
    }
}

juce::AudioProcessorEditor* JamnKitProcessor::createEditor()
{
    return new JamnKitEditor(*this);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new JamnKitProcessor();
}
