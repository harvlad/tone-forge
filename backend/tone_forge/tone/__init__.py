"""Tone Retrieval (Jam-facing wrapper).

Wraps the frozen retrieval algorithm in ``preset_catalog`` /
``rules_engine`` / ``auto_detect`` and produces ``ToneMatch`` shaped
for the HIGH / MEDIUM / LOW / UNKNOWN policy.

Skeleton. The algorithm itself is FROZEN — this subsystem only owns
the Jam-facing contract and the confidence-tier policy boundary.
"""

__all__: list[str] = []
