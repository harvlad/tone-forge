// PluginEditor.cpp — see PluginEditor.h.

#include "PluginEditor.h"

namespace theme
{
// TFTheme parity (mobile Theme.swift / backend _CATEGORY_HEX).
const juce::Colour background { 0xff0b0b0f };
const juce::Colour surface { 0xff17171d };
const juce::Colour stroke { 0xff2a2a33 };
const juce::Colour textPrimary { 0xffe8e8ee };
const juce::Colour textSecondary { 0xff8b8b97 };
const juce::Colour accent { 0xff8b5cf6 };

// Category palette, one row each for the static preview grid.
const juce::Colour category[4] = {
    juce::Colour(0xffef4444),  // DRUMS
    juce::Colour(0xff22c55e),  // BASS
    juce::Colour(0xfff59e0b),  // CHORDS
    juce::Colour(0xfff97316),  // LEAD
};
}  // namespace theme

JamnKitEditor::JamnKitEditor(JamnKitProcessor& p)
    : AudioProcessorEditor(p), processor(p)
{
    setSize(520, 560);
    startTimerHz(8);
}

void JamnKitEditor::paint(juce::Graphics& g)
{
    g.fillAll(theme::background);

    auto area = getLocalBounds().reduced(20);

    // Header: wordmark + milestone tag.
    auto header = area.removeFromTop(56);
    g.setColour(theme::textPrimary);
    g.setFont(juce::Font(juce::FontOptions(34.0f, juce::Font::bold)));
    g.drawText("jamn", header.removeFromLeft(120), juce::Justification::centredLeft);
    g.setColour(theme::textSecondary);
    g.setFont(juce::Font(juce::FontOptions(13.0f)));
    g.drawText("JAM KIT - milestone 0", header, juce::Justification::centredRight);

    area.removeFromTop(8);

    // Host clock readout (proves AudioPlayHead; the milestone-2 grid).
    auto clockRow = area.removeFromTop(30);
    const auto c = processor.hostClock();
    g.setColour(theme::surface);
    g.fillRoundedRectangle(clockRow.toFloat(), 8.0f);
    g.setColour(c.playing ? theme::accent : theme::textSecondary);
    g.setFont(juce::Font(juce::FontOptions(13.0f)));
    juce::String status = c.bpm > 0
        ? juce::String(c.bpm, 1) + " BPM  |  beat " + juce::String(c.ppqPosition, 1)
              + (c.playing ? "  >" : "  ||")
        : juce::String("host clock: waiting");
    g.drawText(status, clockRow.reduced(12, 0), juce::Justification::centredLeft);

    area.removeFromTop(14);

    // Static 4×4 pad preview — MIDI C1..D#2 fires the matching test
    // voice; real pads arrive with the pack loader (milestone 1).
    const int gap = 10;
    const int cell = juce::jmin((area.getWidth() - 3 * gap) / 4,
                                (area.getHeight() - 40 - 3 * gap) / 4);
    auto gridTop = area.getY();
    for (int row = 0; row < 4; ++row)
    {
        for (int col = 0; col < 4; ++col)
        {
            juce::Rectangle<int> r(
                area.getX() + col * (cell + gap),
                gridTop + row * (cell + gap), cell, cell);
            auto base = theme::category[row];
            g.setColour(base.withAlpha(0.30f));
            g.fillRoundedRectangle(r.toFloat(), 10.0f);
            g.setColour(theme::stroke);
            g.drawRoundedRectangle(r.toFloat(), 10.0f, 1.0f);
            g.setColour(base.withAlpha(0.9f));
            g.fillRoundedRectangle(
                (float) r.getX() + 8, (float) r.getBottom() - 12, 26, 3, 1.5f);
            g.setColour(theme::textPrimary.withAlpha(0.85f));
            g.setFont(juce::Font(juce::FontOptions(11.0f, juce::Font::bold)));
            const int note = 36 + (3 - row) * 4 + col;
            g.drawText(juce::MidiMessage::getMidiNoteName(note, true, true, 3),
                       r.reduced(8), juce::Justification::topLeft);
        }
    }

    // Footer.
    g.setColour(theme::textSecondary);
    g.setFont(juce::Font(juce::FontOptions(11.0f)));
    g.drawText("play MIDI C1-D#2  |  kit packs arrive in milestone 1",
               getLocalBounds().removeFromBottom(26),
               juce::Justification::centred);
}
