// PluginEditor.cpp — see PluginEditor.h.

#include "PluginEditor.h"

#include <thread>

namespace theme
{
const juce::Colour background { 0xff0b0b0f };
const juce::Colour surface { 0xff17171d };
const juce::Colour stroke { 0xff2a2a33 };
const juce::Colour textPrimary { 0xffe8e8ee };
const juce::Colour textSecondary { 0xff8b8b97 };
const juce::Colour accent { 0xff8b5cf6 };
const juce::Colour armedAmber { 0xfff59e0b };

const juce::Colour previewRows[4] = {
    juce::Colour(0xffef4444), juce::Colour(0xff22c55e),
    juce::Colour(0xfff59e0b), juce::Colour(0xfff97316),
};

// Layout metrics (logical; editor is resizable).
constexpr int margin = 20;
constexpr int headerH = 56;
constexpr int clockH = 30;
constexpr int knobRowH = 74;
constexpr int footerH = 28;
constexpr int gapS = 8, gapM = 12;
}  // namespace theme

static void themeKnob(juce::Slider& s, juce::Label& l, const juce::String& name)
{
    s.setSliderStyle(juce::Slider::RotaryHorizontalVerticalDrag);
    s.setTextBoxStyle(juce::Slider::NoTextBox, false, 0, 0);
    s.setColour(juce::Slider::rotarySliderFillColourId, theme::accent);
    s.setColour(juce::Slider::rotarySliderOutlineColourId, theme::stroke);
    s.setColour(juce::Slider::thumbColourId, theme::textPrimary);
    l.setText(name, juce::dontSendNotification);
    l.setJustificationType(juce::Justification::centred);
    l.setColour(juce::Label::textColourId, theme::textSecondary);
    l.setFont(juce::Font(juce::FontOptions(10.0f)));
}

JamnKitEditor::JamnKitEditor(JamnKitProcessor& p)
    : AudioProcessorEditor(p), processor(p)
{
    for (auto* b : { &openButton, &browseButton })
    {
        addAndMakeVisible(*b);
        b->setColour(juce::TextButton::buttonColourId, theme::surface);
        b->setColour(juce::TextButton::textColourOffId, theme::textPrimary);
    }
    openButton.onClick = [this] { openPackChooser(); };
    browseButton.onClick = [this] { browseBackend(); };
    browseButton.setColour(juce::TextButton::buttonColourId,
                           theme::accent.withAlpha(0.35f));

    addAndMakeVisible(urlEditor);
    urlEditor.setColour(juce::TextEditor::backgroundColourId, theme::surface);
    urlEditor.setColour(juce::TextEditor::textColourId, theme::textSecondary);
    urlEditor.setColour(juce::TextEditor::outlineColourId, theme::stroke);
    urlEditor.setFont(juce::Font(juce::FontOptions(11.0f)));
    urlEditor.setText(processor.backendUrl(), juce::dontSendNotification);
    urlEditor.onTextChange = [this] {
        processor.setBackendUrl(urlEditor.getText().trim());
    };

    themeKnob(knobFilter, labelFilter, "FILTER");
    themeKnob(knobSpace, labelSpace, "SPACE");
    themeKnob(knobDrive, labelDrive, "DRIVE");
    themeKnob(knobGain, labelGain, "GAIN");
    for (auto* c : std::initializer_list<juce::Component*> {
             &knobFilter, &knobSpace, &knobDrive, &knobGain, &labelFilter,
             &labelSpace, &labelDrive, &labelGain })
        addAndMakeVisible(*c);
    attFilter = std::make_unique<Attachment>(processor.apvts, "filter", knobFilter);
    attSpace = std::make_unique<Attachment>(processor.apvts, "space", knobSpace);
    attDrive = std::make_unique<Attachment>(processor.apvts, "drive", knobDrive);
    attGain = std::make_unique<Attachment>(processor.apvts, "gain", knobGain);

    setResizable(true, true);
    setResizeLimits(440, 520, 1100, 1400);
    setSize(520, 680);
    startTimerHz(30);
}

JamnKitEditor::~JamnKitEditor()
{
    if (worker != nullptr && worker->joinable())
        worker->join();
}

void JamnKitEditor::resized()
{
    using namespace theme;
    auto area = getLocalBounds().reduced(margin);
    auto header = area.removeFromTop(headerH);
    auto buttons = header.removeFromRight(180).withSizeKeepingCentre(180, 30);
    browseButton.setBounds(buttons.removeFromLeft(84));
    buttons.removeFromLeft(8);
    openButton.setBounds(buttons);

    area.removeFromTop(gapS + clockH + gapM);
    auto knobRow = area.removeFromTop(knobRowH);
    urlEditor.setBounds(getLocalBounds()
                            .removeFromBottom(footerH)
                            .reduced(margin, 2)
                            .removeFromLeft(220));
    const int kw = knobRow.getWidth() / 4;
    auto place = [&](juce::Slider& s, juce::Label& l, int i) {
        auto cell = juce::Rectangle<int>(knobRow.getX() + i * kw,
                                         knobRow.getY(), kw, knobRowH);
        l.setBounds(cell.removeFromBottom(14));
        s.setBounds(cell.withSizeKeepingCentre(
            juce::jmin(cell.getWidth(), 56), juce::jmin(cell.getHeight(), 56)));
    };
    place(knobFilter, labelFilter, 0);
    place(knobSpace, labelSpace, 1);
    place(knobDrive, labelDrive, 2);
    place(knobGain, labelGain, 3);
}

// MARK: - Pack sources

void JamnKitEditor::openPackChooser()
{
    statusLine.clear();
    chooser = std::make_unique<juce::FileChooser>(
        "Open a jamn Kit pack (folder or zip)",
        juce::File::getSpecialLocation(juce::File::userHomeDirectory),
        "*.zip");
    const auto flags = juce::FileBrowserComponent::openMode
        | juce::FileBrowserComponent::canSelectFiles
        | juce::FileBrowserComponent::canSelectDirectories;
    chooser->launchAsync(flags, [this](const juce::FileChooser& fc) {
        const auto file = fc.getResult();
        if (file == juce::File())
            return;
        statusLine = processor.loadPack(file);
        repaint();
    });
}

void JamnKitEditor::browseBackend()
{
    if (busy)
        return;
    statusLine = "fetching songs...";
    busy = true;
    const juce::String base = processor.backendUrl();
    auto self = juce::Component::SafePointer<JamnKitEditor>(this);

    if (worker != nullptr && worker->joinable())
        worker->join();
    worker = std::make_unique<std::thread>([self, base] {
        juce::URL url(base + "/api/history?limit=25");
        juce::String body;
        if (auto stream = url.createInputStream(
                juce::URL::InputStreamOptions(
                    juce::URL::ParameterHandling::inAddress)
                    .withConnectionTimeoutMs(8000)))
            body = stream->readEntireStreamAsString();

        juce::MessageManager::callAsync([self, body] {
            if (self == nullptr)
                return;
            self->busy = false;
            const auto parsed = juce::JSON::parse(body);
            auto* history = parsed.getProperty("history", {}).getArray();
            if (history == nullptr || history->isEmpty())
            {
                self->statusLine = "no songs (check backend URL)";
                self->repaint();
                return;
            }
            self->statusLine.clear();
            juce::PopupMenu menu;
            juce::StringArray ids, names;
            int itemId = 1;
            for (const auto& entry : *history)
            {
                const auto name =
                    entry.getProperty("name", "song").toString();
                ids.add(entry.getProperty("id", "").toString());
                names.add(name);
                menu.addItem(itemId++, name.substring(0, 48));
            }
            menu.showMenuAsync(
                juce::PopupMenu::Options()
                    .withTargetComponent(&self->browseButton),
                [self, ids, names](int picked) {
                    if (self == nullptr || picked <= 0)
                        return;
                    self->downloadKit(ids[picked - 1], names[picked - 1]);
                });
            self->repaint();
        });
    });
}

void JamnKitEditor::downloadKit(const juce::String& entryId,
                                const juce::String& name)
{
    if (busy || entryId.isEmpty())
        return;
    busy = true;
    busyStartMs = juce::Time::currentTimeMillis();
    statusLine = "downloading kit: " + name.substring(0, 32) + "...";
    repaint();
    const juce::String base = processor.backendUrl();
    auto self = juce::Component::SafePointer<JamnKitEditor>(this);

    if (worker != nullptr && worker->joinable())
        worker->join();
    worker = std::make_unique<std::thread>([self, base, entryId] {
        juce::URL url(base + "/api/song/" + entryId + "/ableton-kit?pads=16");
        auto dest = juce::File::getSpecialLocation(juce::File::tempDirectory)
                        .getChildFile("jamnKit")
                        .getChildFile("dl_" + entryId + ".zip");
        dest.getParentDirectory().createDirectory();

        bool ok = false;
        if (auto stream = url.createInputStream(
                juce::URL::InputStreamOptions(
                    juce::URL::ParameterHandling::inAddress)
                    // Legacy songs backfill their graph server-side on
                    // the first hit — allow minutes, not seconds.
                    .withConnectionTimeoutMs(600000)))
        {
            juce::FileOutputStream out(dest);
            if (out.openedOk())
            {
                out.setPosition(0);
                out.truncate();
                ok = out.writeFromInputStream(*stream, -1) > 0;
            }
        }

        juce::MessageManager::callAsync([self, dest, ok] {
            if (self == nullptr)
                return;
            self->busy = false;
            self->statusLine = ok ? self->processor.loadPack(dest)
                                  : juce::String("download failed");
            self->repaint();
        });
    });
}

// MARK: - Layout helpers

juce::Rectangle<int> JamnKitEditor::gridArea() const
{
    using namespace theme;
    auto area = getLocalBounds().reduced(margin);
    area.removeFromTop(headerH + gapS + clockH + gapM + knobRowH + gapM);
    area.removeFromBottom(footerH);
    return area;
}

int JamnKitEditor::padIndexAt(juce::Point<int> pos) const
{
    const auto area = gridArea();
    const int gap = 10;
    const int cell = juce::jmin((area.getWidth() - 3 * gap) / 4,
                                (area.getHeight() - 3 * gap) / 4);
    for (int row = 0; row < 4; ++row)
        for (int col = 0; col < 4; ++col)
        {
            juce::Rectangle<int> r(area.getX() + col * (cell + gap),
                                   area.getY() + row * (cell + gap),
                                   cell, cell);
            if (r.contains(pos))
                return (3 - row) * 4 + col;
        }
    return -1;
}

void JamnKitEditor::mouseDown(const juce::MouseEvent& e)
{
    const int pad = padIndexAt(e.getPosition());
    if (pad < 0)
        return;
    mousePad = pad;
    processor.noteOnFromUI(JamnKitProcessor::kFirstNote + pad);
}

void JamnKitEditor::mouseUp(const juce::MouseEvent&)
{
    if (mousePad < 0)
        return;
    processor.noteOffFromUI(JamnKitProcessor::kFirstNote + mousePad);
    mousePad = -1;
}

// MARK: - Paint

void JamnKitEditor::paint(juce::Graphics& g)
{
    using namespace theme;
    g.fillAll(background);
    auto area = getLocalBounds().reduced(margin);
    const auto pack = processor.currentPack();

    // Header.
    auto header = area.removeFromTop(headerH);
    g.setColour(textPrimary);
    g.setFont(juce::Font(juce::FontOptions(34.0f, juce::Font::bold)));
    g.drawText("jamn", header.removeFromLeft(110),
               juce::Justification::centredLeft);
    g.setColour(textSecondary);
    g.setFont(juce::Font(juce::FontOptions(12.0f)));
    g.drawText(pack != nullptr ? pack->songName : juce::String("no pack"),
               header.withTrimmedRight(190),
               juce::Justification::centredRight);

    area.removeFromTop(gapS);

    // Host clock row + live bar sweep (LoopCycleStrip, DAW edition).
    auto clockRow = area.removeFromTop(clockH);
    const auto c = processor.hostClock();
    g.setColour(surface);
    g.fillRoundedRectangle(clockRow.toFloat(), 8.0f);
    if (c.playing)
    {
        g.setColour(accent.withAlpha(0.25f));
        g.fillRoundedRectangle(
            clockRow.toFloat().withWidth(
                (float) clockRow.getWidth() * (float) c.barPhase),
            8.0f);
    }
    g.setColour(c.playing ? accent : textSecondary);
    g.setFont(juce::Font(juce::FontOptions(13.0f)));
    juce::String status = c.bpm > 0
        ? juce::String(c.bpm, 1) + " BPM  |  beat "
              + juce::String(c.ppqPosition, 1) + (c.playing ? "  >" : "  ||")
        : juce::String("host clock: waiting");
    if (pack != nullptr && pack->tempoBpm > 0)
        status << "     song " << juce::String(pack->tempoBpm, 0) << " BPM";
    g.drawText(status, clockRow.reduced(12, 0),
               juce::Justification::centredLeft);

    // Pad grid.
    const auto grid = gridArea();
    const int gap = 10;
    const int cell = juce::jmin((grid.getWidth() - 3 * gap) / 4,
                                (grid.getHeight() - 3 * gap) / 4);
    for (int row = 0; row < 4; ++row)
    {
        for (int col = 0; col < 4; ++col)
        {
            const int padIdx = (3 - row) * 4 + col;
            const int note = JamnKitProcessor::kFirstNote + padIdx;
            juce::Rectangle<int> r(grid.getX() + col * (cell + gap),
                                   grid.getY() + row * (cell + gap),
                                   cell, cell);

            const KitPadSample* pad =
                pack != nullptr ? pack->padForNote(note) : nullptr;
            const bool active = processor.isNoteActive(note);
            const bool armed = processor.isNoteArmed(note);

            juce::Colour base = pad != nullptr ? pad->colour()
                                               : previewRows[row];
            const bool empty = pack != nullptr && pad == nullptr;
            if (empty)
                base = surface;

            g.setColour(
                base.withAlpha(active ? 0.60f : (armed ? 0.42f : 0.26f)));
            g.fillRoundedRectangle(r.toFloat(), 10.0f);

            // Waveform thumbnail across the lower half.
            if (pad != nullptr && !pad->peaks.empty())
            {
                auto wf = r.reduced(8).removeFromBottom(r.getHeight() / 2 - 6);
                const float bw =
                    (float) wf.getWidth() / (float) pad->peaks.size();
                g.setColour(base.withAlpha(active ? 0.95f : 0.55f));
                for (size_t i = 0; i < pad->peaks.size(); ++i)
                {
                    const float h = juce::jmax(
                        1.0f, pad->peaks[i] * (float) wf.getHeight());
                    g.fillRect((float) wf.getX() + (float) i * bw,
                               (float) wf.getCentreY() - h * 0.5f,
                               juce::jmax(1.0f, bw - 1.0f), h);
                }
                // Loop playhead sweep.
                const float phase =
                    processor.padPhase(note - JamnKitProcessor::kFirstNote);
                if (phase >= 0.0f)
                {
                    const float x =
                        (float) wf.getX() + phase * (float) wf.getWidth();
                    g.setColour(juce::Colours::white.withAlpha(0.9f));
                    g.fillRect(x, (float) wf.getY(), 1.5f,
                               (float) wf.getHeight());
                }
            }

            const juce::Colour ring = active
                ? juce::Colours::white.withAlpha(0.85f)
                : (armed ? armedAmber : stroke);
            g.setColour(ring);
            g.drawRoundedRectangle(r.toFloat(), 10.0f,
                                   (active || armed) ? 1.6f : 1.0f);

            g.setColour(textPrimary.withAlpha(empty ? 0.35f : 0.9f));
            g.setFont(juce::Font(juce::FontOptions(11.0f, juce::Font::bold)));
            juce::String label = pad != nullptr
                ? pad->name
                : juce::MidiMessage::getMidiNoteName(note, true, true, 3);
            g.drawFittedText(label,
                             r.reduced(8).removeFromTop(r.getHeight() / 2 - 6),
                             juce::Justification::topLeft, 2);
        }
    }

    // Footer status (right of the URL editor).
    g.setColour(statusLine.isNotEmpty() && !busy
                    ? juce::Colour(0xffef4444)
                    : textSecondary);
    g.setFont(juce::Font(juce::FontOptions(11.0f)));
    juce::String footer = statusLine.isNotEmpty()
        ? statusLine
        : (pack != nullptr ? juce::String("tap pads: loops land on the bar")
                           : juce::String("Browse pulls kits from jamn"));
    if (busy && busyStartMs > 0)
    {
        const auto secs =
            (juce::Time::currentTimeMillis() - busyStartMs) / 1000;
        footer << "  " << juce::String(secs) << "s";
        if (secs > 15)
            footer << " (first export of a song renders server-side"
                      " - can take minutes; repeats are instant)";
    }
    g.drawText(footer,
               getLocalBounds()
                   .removeFromBottom(footerH)
                   .reduced(margin, 0)
                   .withTrimmedLeft(230),
               juce::Justification::centredRight);
}
