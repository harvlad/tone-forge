"""export_ableton_analog: faithful SynthDescriptor → .adv transfer."""

import base64
import gzip
import math
import re

from tone_forge.preset_export import export_ableton_analog


def _descriptor(**overrides):
    base = {
        "oscillator": {
            "type": "square",
            "detune": 10.0,
            "num_voices": 2,
            "sub_osc": True,
            "pulse_width": 0.5,
        },
        "filter": {
            "type": "lowpass",
            "cutoff_hz": 9_000.0,
            "resonance": 0.4,
            "envelope_amount": 0.3,
        },
        "amp_envelope": {
            "attack_ms": 5.0,
            "decay_ms": 250.0,
            "sustain": 0.6,
            "release_ms": 200.0,
        },
    }
    base.update(overrides)
    return base


def _decode(preset):
    xml = gzip.decompress(base64.b64decode(preset.content)).decode("utf-8")
    assert xml.lstrip().startswith("<?xml")
    return xml


def _manuals(xml, name):
    return [float(v) for v in
            re.findall(rf"<{name}>[\s\S]*?<Manual Value=\"([^\"]*)\"", xml)]


def test_roundtrip_is_valid_gzip_xml():
    preset = export_ableton_analog(_descriptor(), preset_name="Song Synth")
    xml = _decode(preset)
    assert "<UltraAnalog>" in xml
    assert preset.filename == "Song Synth.adv"
    assert preset.format == "ableton_analog"


def test_waveform_follows_descriptor():
    for osc_type, expected in [("saw", 1.0), ("square", 2.0), ("sine", 0.0)]:
        d = _descriptor(oscillator={"type": osc_type, "detune": 0, "num_voices": 1})
        xml = _decode(export_ableton_analog(d))
        assert _manuals(xml, "OscillatorWaveShape") == [expected, expected]


def test_cutoff_not_artificially_capped():
    xml = _decode(export_ableton_analog(_descriptor()))
    cutoffs = _manuals(xml, "FilterCutoffFrequency")
    expected = (math.log10(9_000) - math.log10(20)) / (math.log10(18_000) - math.log10(20))
    assert cutoffs == [round(expected, 6)] * 2
    assert cutoffs[0] > 0.75  # old exporter clamped here


def test_zero_resonance_preserved():
    d = _descriptor(filter={"cutoff_hz": 3000, "resonance": 0.0, "envelope_amount": 0.0})
    xml = _decode(export_ableton_analog(d))
    assert _manuals(xml, "FilterQFactor") == [0.0, 0.0]


def test_amp_release_matches_stock_encoding():
    # 200 ms release must land on the stock Analog.adv knob position
    # (~0.5718) under the inferred 0.1 ms – 60 s exponential map.
    xml = _decode(export_ableton_analog(_descriptor()))
    releases = _manuals(xml, "ReleaseTime")
    assert len(releases) == 4
    # occurrences 2 and 4 are the amp envelopes
    assert abs(releases[1] - 0.5718) < 0.005
    assert abs(releases[3] - 0.5718) < 0.005


def test_detune_spread_symmetric():
    xml = _decode(export_ableton_analog(_descriptor()))
    lo, hi = _manuals(xml, "OscillatorDetune")
    assert abs((0.5 - lo) - (hi - 0.5)) < 1e-6
    assert abs((hi - lo) * 100 - 10.0) < 1e-3  # 10 cents total spread


def test_sub_osc_and_env_amount():
    xml = _decode(export_ableton_analog(_descriptor()))
    assert _manuals(xml, "OscillatorSubAmount") == [0.5, 0.5]
    assert _manuals(xml, "FilterEnvCutoffMod") == [0.3, 0.3]
