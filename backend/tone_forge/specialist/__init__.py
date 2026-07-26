"""Experimental specialist analysis pipeline (INTERNAL).

Production-facing seam for Lab-validated specialist models, per
docs/INTENT_DRIVEN_ANALYSIS_ARCHITECTURE.md §5/§21 and
docs/EXPERIMENTAL_SPECIALIST_INTEGRATION.md.

Rules:
- The UI never sees model names; the router maps (engine, family) to
  implementations via the human-promoted specialist_registry.json.
- The Lab never writes the registry; promotion is a reviewed commit.
- Runtime refuses license-blocked entries (see registry.py).
- Register normalization is an explicit pipeline stage with provenance,
  never buried inside a model wrapper.
"""
