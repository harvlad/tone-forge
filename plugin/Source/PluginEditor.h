// PluginEditor.h — jamn Kit editor (milestones 3–5).
//
// TFTheme skin: header (wordmark + song + Browse/Open), host clock row
// with a live bar sweep, macro knob row (Filter/Space/Drive/Gain,
// automatable), and the 4×4 pad grid — waveform thumbnails, loop
// playheads, armed/playing states, mouse-playable. Resizable.
//
// Browse: pulls the song list from the jamn backend (/api/history),
// downloads the picked song's Jam Kit zip (/ableton-kit) on a worker
// thread, and loads it — no manual export step.

#pragma once

#include "PluginProcessor.h"

class JamnKitEditor : public juce::AudioProcessorEditor,
                      private juce::Timer
{
public:
    explicit JamnKitEditor(JamnKitProcessor&);
    ~JamnKitEditor() override;

    void paint(juce::Graphics&) override;
    void resized() override;

    void mouseDown(const juce::MouseEvent&) override;
    void mouseUp(const juce::MouseEvent&) override;

private:
    void timerCallback() override { repaint(); }
    void openPackChooser();
    void browseBackend();
    void downloadKit(const juce::String& entryId, const juce::String& name);
    juce::Rectangle<int> gridArea() const;
    int padIndexAt(juce::Point<int>) const;

    JamnKitProcessor& processor;

    juce::TextButton openButton { "Open..." };
    juce::TextButton browseButton { "Browse" };
    juce::TextEditor urlEditor;
    juce::Slider knobFilter, knobSpace, knobDrive, knobGain;
    juce::Label labelFilter, labelSpace, labelDrive, labelGain;
    using Attachment = juce::AudioProcessorValueTreeState::SliderAttachment;
    std::unique_ptr<Attachment> attFilter, attSpace, attDrive, attGain;

    juce::String statusLine;   // footer: errors / download progress
    bool busy = false;         // network in flight
    juce::int64 busyStartMs = 0;
    int mousePad = -1;
    std::unique_ptr<juce::FileChooser> chooser;
    std::unique_ptr<std::thread> worker;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(JamnKitEditor)
};
