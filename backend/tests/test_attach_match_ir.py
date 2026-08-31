"""attach_match_ir: cab → IR block swap in exported .hlx JSON."""

import json

from tone_forge.preset_export import attach_match_ir


def _hlx(blocks):
    return json.dumps({
        "version": 6,
        "schema": "L6Preset",
        "data": {"tone": {"dsp0": blocks, "dsp1": {}}},
    })


def _cab_block(position=3):
    return {
        "@enabled": True,
        "@model": "HD2_Cab4x12Greenback25",
        "@position": position,
        "@path": 0,
        "@type": 2,
        "Level": 0.0,
    }


def test_replaces_cab_block_preserving_position():
    content = _hlx({"block0": {"@model": "HD2_AmpBritPlexi", "@position": 2},
                    "block1": _cab_block(position=3)})
    out = json.loads(attach_match_ir(content, ir_index=7, level_db=-3.0))
    block = out["data"]["tone"]["dsp0"]["block1"]
    assert block["@model"] == "HD2_ImpulseResponse2048"
    assert block["Index"] == 7
    assert block["Level"] == -3.0
    assert block["@position"] == 3
    assert block["Mix"] == 1.0
    # amp untouched
    assert out["data"]["tone"]["dsp0"]["block0"]["@model"] == "HD2_AmpBritPlexi"


def test_uses_empty_slot_when_no_cab():
    content = _hlx({"block0": {"@model": "HD2_AmpBritPlexi", "@position": 0},
                    "block1": {}})
    out = json.loads(attach_match_ir(content))
    assert out["data"]["tone"]["dsp0"]["block1"]["@model"] == "HD2_ImpulseResponse2048"


def test_unchanged_when_no_slot():
    content = _hlx({"block0": {"@model": "HD2_AmpBritPlexi", "@position": 0}})
    assert attach_match_ir(content) == content


def test_ir_index_clamped_to_valid_range():
    content = _hlx({"block0": _cab_block()})
    out = json.loads(attach_match_ir(content, ir_index=999))
    assert out["data"]["tone"]["dsp0"]["block0"]["Index"] == 128
