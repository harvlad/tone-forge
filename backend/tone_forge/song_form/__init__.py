"""Song-form structural-signal extractors.

Subpackage for the Phase-0C/D structural-signal toolkit. The first
inhabitant is the H2 chord-trigram recurrence extractor, frozen by
`backend/h2_specification.md` (the Decision-B primary structural
signal from `backend/signal_taxonomy_report.md`).

This subpackage is deliberately decoupled from `analysis/` so the
structural-signal pipeline can evolve without entangling chord
detection or section-boundary inference.
"""

from tone_forge.song_form.h2 import H2Result, extract_h2
from tone_forge.song_form.role_classifier import (
    RoleDecision,
    RoleThresholds,
    classify_roles,
)

__all__ = [
    "H2Result",
    "extract_h2",
    "RoleDecision",
    "RoleThresholds",
    "classify_roles",
]
