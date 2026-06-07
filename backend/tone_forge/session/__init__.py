"""Session Engine: canonical TransportState owner.

UI dispatches intents; this engine produces ``TransportState``; Connect
and the UI subscribe. There is no other source of truth for transport.

Priority 5 of ``/EXECUTION_PLAN.md`` lands this in stages:

* ``protocol`` — v1 message schema (this commit).
* ``transport`` — TransportState reducer.
* ``bundle`` — SessionBundle assembly from existing pipeline output.

Legacy code paths in ``tone_forge_api.py`` and ``unified_pipeline.py``
keep functioning during the transition; this package replaces them
incrementally as each stage lands.
"""

from tone_forge.session import protocol
from tone_forge.session.protocol import (
    MessageType,
    PROTOCOL_VERSION,
    envelope,
    is_supported_version,
)

__all__ = [
    "MessageType",
    "PROTOCOL_VERSION",
    "envelope",
    "is_supported_version",
    "protocol",
]
