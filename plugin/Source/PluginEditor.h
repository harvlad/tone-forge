// PluginEditor.h — jamn Kit milestone 0 editor.
//
// The seed of the proprietary skin: TFTheme dark surface, jamn
// wordmark, category-colored 4×4 pad preview (static for now), and a
// live host-clock readout proving AudioPlayHead works — the grid the
// milestone-2 launch quantize will run on.

#pragma once

#include "PluginProcessor.h"

class JamnKitEditor : public juce::AudioProcessorEditor,
                      private juce::Timer
{
public:
    explicit JamnKitEditor(JamnKitProcessor&);

    void paint(juce::Graphics&) override;
    void resized() override {}

private:
    void timerCallback() override { repaint(); }

    JamnKitProcessor& processor;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(JamnKitEditor)
};
