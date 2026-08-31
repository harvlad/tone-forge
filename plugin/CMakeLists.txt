# jamn Kit plugin — milestone 0 skeleton (docs/jamn-vst-plugin-scope.md).
#
# One generic skinned plugin; songs arrive as kit packs (kit.json +
# Samples/) in later milestones. This target just has to LOAD in a DAW
# with the jamn-branded editor and make a test tone on MIDI input.
#
# Build:
#   cmake -B build -DCMAKE_BUILD_TYPE=Release
#   cmake --build build --target JamnKit_All -j
# Products land in build/JamnKit_artefacts/Release/{VST3,AU,Standalone}.
#
# JUCE license: GPLv3 while unlicensed (fine for dev); buy the Indie
# tier before shipping closed-source.

cmake_minimum_required(VERSION 3.24)
project(JamnKit VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OSX_DEPLOYMENT_TARGET "12.0" CACHE STRING "")

include(FetchContent)
FetchContent_Declare(
    JUCE
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG 8.0.6
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(JUCE)

juce_add_plugin(JamnKit
    COMPANY_NAME "jamn"
    BUNDLE_ID "com.harvlad.jamnkit"
    IS_SYNTH TRUE
    NEEDS_MIDI_INPUT TRUE
    NEEDS_MIDI_OUTPUT FALSE
    PLUGIN_MANUFACTURER_CODE Jamn
    PLUGIN_CODE Jkit
    FORMATS AU VST3 Standalone
    PRODUCT_NAME "jamn Kit")

target_sources(JamnKit PRIVATE
    Source/KitPack.cpp
    Source/PluginProcessor.cpp
    Source/PluginEditor.cpp)

target_compile_definitions(JamnKit PUBLIC
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0
    JUCE_DISPLAY_SPLASH_SCREEN=1)

target_link_libraries(JamnKit
    PRIVATE
        juce::juce_audio_utils
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags)
