"""Device Discovery + Integration: produce ``DeviceCaps``.

Discovery answers "what is the user playing through" and yields
``DeviceCaps``. Integration adapters consume those caps to decide
between preset delivery and curated monitor chain fallback.

Skeleton. MVP scope: INTERFACE_ONLY vs. modeler-class branching only.
"""

from tone_forge.devices.discovery import probe
from tone_forge.devices.preferences import (
    clear_preferences,
    load_preferences,
    preferences_path,
    save_preferences,
)

__all__: list[str] = [
    "probe",
    "load_preferences",
    "save_preferences",
    "clear_preferences",
    "preferences_path",
]
