"""Rules engine: ToneDescriptor -> ranked block candidates.

This module is hardware-agnostic. It works in terms of the descriptor's
abstract families (`amp.family`, `cab.speaker_character`, effect `type`s)
and returns block IDs from a catalog. Per-device translators
(`helix_translator.py`, future `fractal_translator.py`) load their
catalog and call into this module.

A "rule" maps a descriptor predicate -> a list of candidate block IDs
with priority weights. Multiple rules can fire; we sort and keep the
top match per slot.

Design notes:
- Keep rules data-driven where possible so non-engineers can tune them.
- Always return a fallback when the family is "unknown".
- Confidence from the descriptor flows into the chain card as hedging
  text, not into block selection itself (low confidence still gets a
  pick — the UI just communicates uncertainty).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .descriptor import ToneDescriptor


@dataclass
class BlockPick:
    """One chosen block + the params the engine wants set on it."""
    slot: str            # "amp" | "cab" | "drive" | "delay" | "reverb" | "modulation"
    block_id: str
    display: str
    params: dict         # parameter name -> value (display units, 0-10 etc.)
    rationale: str       # one-line reason this was chosen
    block_family: str = None  # normalized family for plugin matching (e.g., "marshall_plexi", "reverb_plate")


@dataclass
class ChainCard:
    """The translator's output. Ordered list of blocks + hints for the user."""
    picks: list[BlockPick]
    tweak_hints: list[str]


def pick_amp(d: ToneDescriptor, catalog: list[dict]) -> BlockPick:
    """Pick an amp block from the catalog using descriptor.amp.family."""
    family = d.amp.family
    candidates = [a for a in catalog if family in a.get("families", [])]
    chosen = candidates[0] if candidates else _fallback_amp(catalog)

    # Map normalized voicing (0-1) onto Helix's 0-10 display range.
    v = d.amp.voicing
    params = {
        "drive":    round(d.amp.gain * 10, 1),
        "bass":     round(v.bass * 10, 1),
        "mid":      round(v.mid * 10, 1),
        "treble":   round(v.treble * 10, 1),
        "presence": round(v.presence * 10, 1),
        "master":   6.5,  # leave some headroom; users tune to taste
    }
    return BlockPick(
        slot="amp",
        block_id=chosen["id"],
        display=chosen["display"],
        params=params,
        rationale=f"Amp family detected as {family}; matched on the `families` index.",
        block_family=family,  # e.g., "marshall_plexi", "fender_blackface"
    )


def pick_cab(d: ToneDescriptor, catalog: list[dict]) -> BlockPick:
    """Pick a cab matching configuration first, then speaker character."""
    char = d.cab.speaker_character
    config = d.cab.configuration

    by_char = [c for c in catalog if c["speaker_character"] == char]
    by_config = [c for c in by_char if c["configuration"] == config]
    chosen = (by_config or by_char or catalog)[0]

    actual_config = chosen["configuration"]
    if actual_config == config:
        rationale = f"Cab character {char} / {config}."
    else:
        rationale = (
            f"Cab character {char} (catalog has no {config} variant; "
            f"using {actual_config} with the same speaker)."
        )

    return BlockPick(
        slot="cab",
        block_id=chosen["id"],
        display=chosen["display"],
        params={"mic": "57 Dynamic", "distance": 1.0, "low_cut": 80, "high_cut": 9000},
        rationale=rationale,
        block_family="cab_ir",  # cab/IR loader
    )


def pick_drive(d: ToneDescriptor, catalog: list[dict]) -> Optional[BlockPick]:
    od = d.effects.overdrive_pedal
    if not od or od.drive < 0.05:
        return None
    match = next((b for b in catalog if b["style"] == od.style), catalog[0])
    return BlockPick(
        slot="drive",
        block_id=match["id"],
        display=match["display"],
        params={"drive": round(od.drive * 10, 1), "tone": 5.5, "level": round(od.level * 10, 1)},
        rationale=f"Detected {od.style} overdrive in front of amp.",
        block_family=f"overdrive_{od.style}",  # e.g., "overdrive_tube_screamer"
    )


def pick_delay(d: ToneDescriptor, catalog: list[dict]) -> Optional[BlockPick]:
    dl = d.effects.delay
    if not dl or dl.type == "none" or dl.mix < 0.03:
        return None
    match = next((b for b in catalog if b["type"] == dl.type), catalog[0])
    return BlockPick(
        slot="delay",
        block_id=match["id"],
        display=match["display"],
        params={"time_ms": round(dl.time_ms), "feedback": round(dl.feedback * 10, 1), "mix": round(dl.mix * 100)},
        rationale=f"{dl.type} delay around {round(dl.time_ms)}ms.",
        block_family=f"delay_{dl.type}",  # e.g., "delay_tape", "delay_digital"
    )


def pick_reverb(d: ToneDescriptor, catalog: list[dict]) -> Optional[BlockPick]:
    rv = d.effects.reverb
    if not rv or rv.type == "none" or rv.mix < 0.03:
        return None
    match = next((b for b in catalog if b["type"] == rv.type), catalog[0])
    return BlockPick(
        slot="reverb",
        block_id=match["id"],
        display=match["display"],
        params={"decay": round(rv.size * 10, 1), "mix": round(rv.mix * 100)},
        rationale=f"{rv.type} reverb, moderate size.",
        block_family=f"reverb_{rv.type}",  # e.g., "reverb_plate", "reverb_hall"
    )


def pick_modulation(d: ToneDescriptor, catalog: list[dict]) -> Optional[BlockPick]:
    mod = d.effects.modulation
    if not mod or mod.type == "none" or mod.depth < 0.05:
        return None
    match = next((b for b in catalog if b["type"] == mod.type), catalog[0])
    return BlockPick(
        slot="modulation",
        block_id=match["id"],
        display=match["display"],
        params={"rate": round(mod.rate * 10, 1), "depth": round(mod.depth * 10, 1), "mix": 50},
        rationale=f"{mod.type} modulation detected.",
        block_family=f"modulation_{mod.type}",  # e.g., "modulation_chorus", "modulation_flanger"
    )


def pick_amp_alternates(d: ToneDescriptor, catalog: list[dict]) -> list[BlockPick]:
    """Build BlockPicks for the runner-up amp families when confidence is low.

    Surfaces up to 2 alternates so the user can A/B audition. Only fires
    when the primary confidence is below 0.7 (otherwise the picks clutter
    the chain card without earning their space).
    """
    if d.confidence.amp_family >= 0.7 or not d.amp.alternates:
        return []

    picks: list[BlockPick] = []
    for alt in d.amp.alternates[:2]:
        family = alt["family"]
        candidates = [a for a in catalog if family in a.get("families", [])]
        if not candidates:
            continue
        chosen = candidates[0]
        v = d.amp.voicing
        params = {
            "drive":    round(d.amp.gain * 10, 1),
            "bass":     round(v.bass * 10, 1),
            "mid":      round(v.mid * 10, 1),
            "treble":   round(v.treble * 10, 1),
            "presence": round(v.presence * 10, 1),
            "master":   6.5,
        }
        picks.append(BlockPick(
            slot="amp_alt",
            block_id=chosen["id"],
            display=chosen["display"],
            params=params,
            rationale=f"Alternate to audition: {family} (score {alt['score']:.2f}).",
            block_family=family,  # alternate amp family
        ))
    return picks


# ---------------------------------------------------------------------------
# Amp-family-aware tweak hints.
#
# Earlier versions fired "dark pickup" and "heavy mid-scoop" hints any time
# the values crossed an absolute threshold, which lied on natural clean
# signals (where bass-heavy, treble-light is the *expected* shape, not a
# deliberate scoop or a dark pickup). Now hints check whether the value is
# unusual *for the picked amp family*.

_BRIGHT_FAMILIES = {"fender_clean", "vox_chime", "ac30"}
_MID_FORWARD_FAMILIES = {
    "marshall_plexi", "marshall_jcm", "tweed", "dumble", "vox_chime", "ac30",
}
_DARK_FAMILIES = {"mesa_rectifier", "5150_peavey", "bogner", "soldano"}


def tweak_hints(d: ToneDescriptor) -> list[str]:
    """Hints that flag things genuinely worth telling the user about.

    Most hints are conditioned on whether the measured value is surprising
    given the amp family — i.e. it would actually be worth tweaking. We
    leave bog-standard "clean amp sounds dark" type signals alone.
    """
    hints: list[str] = []
    family = d.amp.family

    # Brightness only flag-worthy if it conflicts with what the amp suggests.
    if d.guitar.pickup_brightness < 0.2 and family in _BRIGHT_FAMILIES:
        hints.append(
            "Pickups read dark for a bright amp family — try Presence +0.5 "
            "and Treble +0.5 if the tone feels muffled."
        )
    elif d.guitar.pickup_brightness > 0.8 and family in _DARK_FAMILIES:
        hints.append(
            "Pickups read bright for a high-gain amp — pull Treble down ~1 "
            "to tame fizz, and consider a darker cab if it still bites."
        )

    # Mid-scoop only flag-worthy if the picked amp wouldn't normally be scooped.
    if d.amp.voicing.mid_scoop > 0.8 and family in _MID_FORWARD_FAMILIES:
        hints.append(
            "Mid scoop is heavy for this amp family — push Mid up to 5-6 "
            "if the tone disappears in a band mix."
        )

    if d.confidence.amp_family < 0.6:
        hints.append(
            "Amp family confidence is low — alternate picks shown below are "
            "worth A/B'ing against the primary."
        )
    if d.confidence.cab < 0.5:
        hints.append(
            "Cab character was guessed from the amp family rather than the "
            "audio (upper-band signal was too quiet to read directly)."
        )

    return hints


def _fallback_amp(catalog: list[dict]) -> dict:
    """Pick a sensible default when family is unknown."""
    return catalog[0]
