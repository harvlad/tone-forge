"""Helix translator: ToneDescriptor -> Helix ChainCard.

Loads the Helix block catalog and calls the hardware-agnostic
rules engine for each slot. Order of blocks in the returned card
follows the typical Helix signal flow:

    drive -> amp -> cab -> modulation -> delay -> reverb
"""
from __future__ import annotations

import json
from pathlib import Path

from . import rules_engine as rules
from .descriptor import ToneDescriptor

_CATALOG_PATH = Path(__file__).parent.parent / "data" / "helix_blocks.json"


def _load_catalog() -> dict:
    with open(_CATALOG_PATH) as f:
        return json.load(f)


def translate(descriptor: ToneDescriptor) -> rules.ChainCard:
    cat = _load_catalog()

    picks: list[rules.BlockPick] = []

    drive = rules.pick_drive(descriptor, cat["drives"])
    if drive:
        picks.append(drive)

    picks.append(rules.pick_amp(descriptor, cat["amps"]))
    # If confidence is low, surface 1-2 alternate amp picks for A/B.
    picks.extend(rules.pick_amp_alternates(descriptor, cat["amps"]))
    picks.append(rules.pick_cab(descriptor, cat["cabs"]))

    mod = rules.pick_modulation(descriptor, cat["modulation"])
    if mod:
        picks.append(mod)

    delay = rules.pick_delay(descriptor, cat["delays"])
    if delay:
        picks.append(delay)

    reverb = rules.pick_reverb(descriptor, cat["reverbs"])
    if reverb:
        picks.append(reverb)

    return rules.ChainCard(picks=picks, tweak_hints=rules.tweak_hints(descriptor))
