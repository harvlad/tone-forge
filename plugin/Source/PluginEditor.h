// PluginEditor.h — jamn Kit milestone 1 editor.
//
// TFTheme skin: header (wordmark + song + Open Pack), host-clock row,
// and the 4×4 pad grid — real pack pads (name, category color, active
// glow, mouse-playable) or the static preview when no pack is loaded.

#pragma once

#include "PluginProcessor.h"

class JamnKitEditor : public juce::AudioProcessorEditor,
                      private juce::Timer
{
public:
    explicit JamnKitEditor(JamnKitProcessor&);

    void paint(juce::Graphics&) override;
    void resized() override;

    void mouseDown(const juce::MouseEvent&) override;
    void mouseUp(const juce::MouseEvent&) override;

private:
    void timerCallback() override { repaint(); }
    void openPackChooser();
    juce::Rectangle<int> gridArea() const;
    int padIndexAt(juce::Point<int>) const;   // -1 = none

    JamnKitProcessor& processor;
    juce::TextButton openButton { "Open Pack..." };
    juce::String lastError;
    int mousePad = -1;
    std::unique_ptr<juce::FileChooser> chooser;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(JamnKitEditor)
};
