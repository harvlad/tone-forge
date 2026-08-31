// KitPack.cpp — see KitPack.h.

#include "KitPack.h"

juce::Colour KitPadSample::colour() const
{
    // Backend _CATEGORY_HEX parity — same hues as the app + .adg.
    static const std::map<juce::String, juce::Colour> map = {
        { "DRUMS", juce::Colour(0xffef4444) },
        { "BASS", juce::Colour(0xff22c55e) },
        { "CHORDS", juce::Colour(0xfff59e0b) },
        { "LEAD", juce::Colour(0xfff97316) },
        { "VOCAL", juce::Colour(0xffec4899) },
        { "RHYTHM", juce::Colour(0xff3b82f6) },
        { "TEXTURE", juce::Colour(0xff06b6d4) },
        { "FX", juce::Colour(0xffa855f7) },
        { "STAB", juce::Colour(0xff8b5cf6) },
    };
    const auto it = map.find(category);
    return it != map.end() ? it->second : juce::Colour(0xff6b7280);
}

namespace kitpack
{

/// Bake a ~15 ms equal-power crossfade into a loop buffer: the tail is
/// blended into the head and then trimmed, so a hard wrap at the new
/// end is seamless (same idea as the app's SeamlessLoop).
static void bakeLoopCrossfade(juce::AudioBuffer<float>& audio,
                              double sampleRate)
{
    const int fade = juce::jmin((int) (0.015 * sampleRate),
                                audio.getNumSamples() / 4);
    if (fade < 8)
        return;
    const int newLength = audio.getNumSamples() - fade;
    for (int ch = 0; ch < audio.getNumChannels(); ++ch)
    {
        auto* data = audio.getWritePointer(ch);
        for (int i = 0; i < fade; ++i)
        {
            const float t = (float) i / (float) fade;
            const float in = std::sqrt(t);
            const float out = std::sqrt(1.0f - t);
            data[i] = data[i] * in + data[newLength + i] * out;
        }
    }
    audio.setSize(audio.getNumChannels(), newLength, true, true, true);
}

static juce::File findPackRoot(const juce::File& dir)
{
    // The kit.json may sit in `dir` itself or one level down (zip
    // extraction keeps the "{Song} Jam Kit/" wrapper folder).
    if (dir.getChildFile("kit.json").existsAsFile())
        return dir;
    for (const auto& child : dir.findChildFiles(
             juce::File::findDirectories, false))
        if (child.getChildFile("kit.json").existsAsFile())
            return child;
    return {};
}

std::shared_ptr<const LoadedPack> load(const juce::File& source,
                                       juce::String& error)
{
    juce::File root = source;

    if (source.existsAsFile()
        && source.getFileExtension().equalsIgnoreCase(".zip"))
    {
        juce::ZipFile zip(source);
        auto temp = juce::File::getSpecialLocation(juce::File::tempDirectory)
                        .getChildFile("jamnKit")
                        .getChildFile(juce::String(
                            source.getFileNameWithoutExtension().hashCode()));
        temp.createDirectory();
        if (auto result = zip.uncompressTo(temp, true); result.failed())
        {
            error = "Could not unzip pack: " + result.getErrorMessage();
            return nullptr;
        }
        root = temp;
    }

    root = findPackRoot(root);
    if (root == juce::File())
    {
        error = "No kit.json found - pick the Jam Kit folder or zip.";
        return nullptr;
    }

    const auto parsed = juce::JSON::parse(
        root.getChildFile("kit.json").loadFileAsString());
    if (parsed.isVoid())
    {
        error = "kit.json is unreadable.";
        return nullptr;
    }

    auto pack = std::make_shared<LoadedPack>();
    pack->sourcePath = source.getFullPathName();
    pack->songName = parsed.getProperty("songName", juce::String()).toString();
    pack->tempoBpm = (double) parsed.getProperty("tempoBpm", 0.0);
    if (pack->songName.isEmpty())
        pack->songName = root.getFileName();

    juce::AudioFormatManager formats;
    formats.registerBasicFormats();

    auto loadEntry = [&](const juce::var& entry, int fallbackNote) -> bool {
        juce::String rel = entry.getProperty("file", juce::String()).toString();
        if (rel.isEmpty())
            return false;
        auto file = root.getChildFile(rel);
        std::unique_ptr<juce::AudioFormatReader> reader(
            formats.createReaderFor(file));
        if (reader == nullptr)
            return false;

        KitPadSample pad;
        pad.name = entry.getProperty("name", file.getFileNameWithoutExtension())
                       .toString();
        pad.category = entry.getProperty("category", juce::String()).toString();
        pad.midiNote = (int) entry.getProperty("midiNote", fallbackNote);
        pad.loopable = (bool) entry.getProperty("loopable", true);
        pad.sourceSampleRate = reader->sampleRate;
        pad.audio.setSize((int) reader->numChannels,
                          (int) reader->lengthInSamples);
        reader->read(&pad.audio, 0, (int) reader->lengthInSamples, 0,
                     true, true);
        if (pad.loopable)
            bakeLoopCrossfade(pad.audio, pad.sourceSampleRate);
        pack->pads.push_back(std::move(pad));
        return true;
    };

    if (auto* samples = parsed.getProperty("samples", {}).getArray())
    {
        int note = 36;
        for (const auto& entry : *samples)
            if (loadEntry(entry, note))
                ++note;
    }
    else
    {
        // Legacy zips (pre-manifest): filename order = MIDI order.
        auto files = root.getChildFile("Samples").findChildFiles(
            juce::File::findFiles, false, "*.wav");
        files.sort();
        int note = 36;
        for (const auto& f : files)
        {
            juce::DynamicObject::Ptr obj = new juce::DynamicObject();
            obj->setProperty("file", "Samples/" + f.getFileName());
            obj->setProperty("name", f.getFileNameWithoutExtension()
                                         .fromFirstOccurrenceOf(" ", false, false));
            if (loadEntry(juce::var(obj.get()), note))
                ++note;
        }
    }

    if (pack->pads.empty())
    {
        error = "Pack has no readable samples.";
        return nullptr;
    }
    return pack;
}

}  // namespace kitpack
