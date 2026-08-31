// PluginEditor.cpp — see PluginEditor.h.

#include "PluginEditor.h"

namespace theme
{
const juce::Colour background { 0xff0b0b0f };
const juce::Colour surface { 0xff17171d };
const juce::Colour stroke { 0xff2a2a33 };
const juce::Colour textPrimary { 0xffe8e8ee };
const juce::Colour textSecondary { 0xff8b8b97 };
const juce::Colour accent { 0xff8b5cf6 };

const juce::Colour previewRows[4] = {
    juce::Colour(0xffef4444), juce::Colour(0xff22c55e),
    juce::Colour(0xfff59e0b), juce::Colour(0xfff97316),
};
}  // namespace theme

JamnKitEditor::JamnKitEditor(JamnKitProcessor& p)
    : AudioProcessorEditor(p), processor(p)
{
    addAndMakeVisible(openButton);
    openButton.setColour(juce::TextButton::buttonColourId, theme::surface);
    openButton.setColour(juce::TextButton::textColourOffId, theme::textPrimary);
    openButton.onClick = [this] { openPackChooser(); };
    setSize(520, 600);
    startTimerHz(15);
}

void JamnKitEditor::resized()
{
    openButton.setBounds(getWidth() - 130, 24, 106, 30);
}

void JamnKitEditor::openPackChooser()
{
    lastError.clear();  // stale errors outlived successful loads
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
        lastError = processor.loadPack(file);
        repaint();
    });
}

juce::Rectangle<int> JamnKitEditor::gridArea() const
{
    auto area = getLocalBounds().reduced(20);
    area.removeFromTop(56 + 8 + 30 + 14);
    area.removeFromBottom(28);
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
                // Screen top row = highest notes (pad 12..15), like a
                // drum rack: bottom-left = C1.
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

void JamnKitEditor::paint(juce::Graphics& g)
{
    g.fillAll(theme::background);
    auto area = getLocalBounds().reduced(20);
    const auto pack = processor.currentPack();

    // Header.
    auto header = area.removeFromTop(56);
    g.setColour(theme::textPrimary);
    g.setFont(juce::Font(juce::FontOptions(34.0f, juce::Font::bold)));
    g.drawText("jamn", header.removeFromLeft(110),
               juce::Justification::centredLeft);
    g.setColour(theme::textSecondary);
    g.setFont(juce::Font(juce::FontOptions(12.0f)));
    juce::String subtitle = pack != nullptr
        ? pack->songName
        : juce::String("no pack loaded");
    g.drawText(subtitle, header.withTrimmedRight(120),
               juce::Justification::centredRight);

    area.removeFromTop(8);

    // Host clock row.
    auto clockRow = area.removeFromTop(30);
    const auto c = processor.hostClock();
    g.setColour(theme::surface);
    g.fillRoundedRectangle(clockRow.toFloat(), 8.0f);
    g.setColour(c.playing ? theme::accent : theme::textSecondary);
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
            // Armed = queued for the next host bar; orange ring says
            // "waiting for the beat", not broken (app UX parity).
            const bool armed = processor.isNoteArmed(note);

            juce::Colour base = pad != nullptr
                ? pad->colour()
                : theme::previewRows[row];
            const bool empty = pack != nullptr && pad == nullptr;
            if (empty)
                base = theme::surface;

            g.setColour(base.withAlpha(active ? 0.75f : (armed ? 0.45f : 0.30f)));
            g.fillRoundedRectangle(r.toFloat(), 10.0f);
            const juce::Colour ring = active
                ? juce::Colours::white.withAlpha(0.85f)
                : (armed ? juce::Colour(0xfff59e0b) : theme::stroke);
            g.setColour(ring);
            g.drawRoundedRectangle(r.toFloat(), 10.0f,
                                   (active || armed) ? 1.6f : 1.0f);
            if (!empty)
            {
                g.setColour(base.withAlpha(0.9f));
                g.fillRoundedRectangle((float) r.getX() + 8,
                                       (float) r.getBottom() - 12, 26, 3, 1.5f);
            }

            g.setColour(theme::textPrimary.withAlpha(empty ? 0.35f : 0.9f));
            g.setFont(juce::Font(juce::FontOptions(11.0f, juce::Font::bold)));
            juce::String label = pad != nullptr
                ? pad->name
                : juce::MidiMessage::getMidiNoteName(note, true, true, 3);
            g.drawFittedText(label, r.reduced(8), juce::Justification::topLeft, 3);
        }
    }

    // Footer.
    g.setColour(lastError.isNotEmpty() ? juce::Colour(0xffef4444)
                                       : theme::textSecondary);
    g.setFont(juce::Font(juce::FontOptions(11.0f)));
    const juce::String footer = lastError.isNotEmpty()
        ? lastError
        : (pack != nullptr
               ? juce::String("click pads or play MIDI C1-D#2")
               : juce::String(
                     "Open Pack... loads a Jam Kit zip from jamn"));
    g.drawText(footer, getLocalBounds().removeFromBottom(26),
               juce::Justification::centred);
}
