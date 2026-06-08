"""Device Discovery + Integration: produce ``DeviceCaps``.

Discovery answers "what is the user playing through" and yields
``DeviceCaps``. Integration adapters consume those caps to decide
between preset delivery and curated monitor chain fallback.

Skeleton. MVP scope: INTERFACE_ONLY vs. modeler-class branching only.
"""

from tone_forge.devices.discovery import probe

__all__: list[str] = ["probe"]
