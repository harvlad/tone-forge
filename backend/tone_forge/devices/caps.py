"""Map ``DeviceClass`` and ``DevicePreferences`` to ``DeviceCaps``.

This is the seam between Discovery (which captures *what the user
plays through*) and the rest of the system (which only needs to know
*what capabilities that path supports*). The API edge calls these
helpers when building a ``SessionBundle`` so the bundle's
``device_caps`` field reflects the persisted onboarding answer
instead of always defaulting to interface-only.

Per EXECUTION_PLAN.md §8, MVP only branches on INTERFACE_ONLY vs.
modeler-class devices: every class is ``can_monitor=True`` and
``can_receive_preset=False`` for now (preset export adapters are
deferred to Phase 2 per §10). ``NO_HARDWARE`` is the one exception —
no monitoring path exists when there's no audio interface.

The display strings are the user-facing labels shown on the tone
card and in device settings. They mirror the §8 onboarding prompt
exactly so the user sees the same wording end-to-end.
"""

from __future__ import annotations

from typing import Optional

from tone_forge.contracts import DeviceCaps, DeviceClass, DevicePreferences

# Per §8 onboarding prompt: the exact strings shown next to each radio
# button. Keep these aligned with `static/jam.html` when the
# onboarding screen lands.
_DISPLAY_NAMES: dict[DeviceClass, str] = {
    DeviceClass.INTERFACE_ONLY: "Audio interface",
    DeviceClass.HELIX: "Line 6 Helix",
    DeviceClass.QUAD_CORTEX: "Neural DSP Quad Cortex",
    DeviceClass.KEMPER: "Kemper",
    DeviceClass.FRACTAL: "Fractal",
    DeviceClass.TONEX: "IK Multimedia Tonex",
    DeviceClass.NEURAL_DSP: "Neural DSP plugin",
    DeviceClass.CONNECT_MONITOR: "Connect monitor",
    DeviceClass.NO_HARDWARE: "No hardware",
    DeviceClass.OTHER: "Other",
}


def caps_from_class(cls: DeviceClass) -> DeviceCaps:
    """Project a ``DeviceClass`` into the matching ``DeviceCaps``.

    Every modeler class advertises ``can_monitor=True`` and
    ``can_receive_preset=False`` at MVP — adapters that would flip
    ``can_receive_preset`` are Phase 2 per §10.
    """
    display = _DISPLAY_NAMES.get(cls, "Other")
    can_monitor = cls is not DeviceClass.NO_HARDWARE
    return DeviceCaps(
        cls=cls,
        display_name=display,
        can_monitor=can_monitor,
        can_receive_preset=False,
    )


def caps_from_preferences(
    prefs: Optional[DevicePreferences],
) -> Optional[DeviceCaps]:
    """Project persisted ``DevicePreferences`` into ``DeviceCaps``.

    Returns ``None`` when ``prefs`` is ``None`` so the session bundle
    builder can fall back to its own interface-only default. Carries
    ``preferred_chain_family`` forward when the user pinned one so
    retrieval can prefer that family in LOW/UNKNOWN tier fallbacks.
    """
    if prefs is None:
        return None

    base = caps_from_class(prefs.device_class)
    return DeviceCaps(
        cls=base.cls,
        display_name=base.display_name,
        can_monitor=base.can_monitor,
        can_receive_preset=base.can_receive_preset,
        preferred_chain_family=prefs.preferred_chain_family,
        vendor_hint=base.vendor_hint,
        model_hint=base.model_hint,
    )
