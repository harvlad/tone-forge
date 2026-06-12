# Tone

**Purpose**: Wrap the frozen retrieval stack and emit `ToneMatch` (tier + chosen/alternates or fallback chain id) for Jam. The retrieval algorithm itself is frozen; this layer owns the tier policy.

**Owner**: Platform & Engine attention pool.

**Status**: Code surface landed; fitted artifact blocked. `calibration.py` (calibrator + isotonic loader), `tiers.py` (tier policy), `policy.py` (fallback chain selection), `guitar_catalog.py` (per-instrument matcher), `instrumentation.py` (analytics surface) all in place. Loader infrastructure activates a fitted curve the moment `tone/calibration_v1.joblib` lands; that artifact is blocked on 100 hand-labeled clips (external data collection — the only remaining P6 gate). See EXECUTION_PLAN.md §7 + §9 items 31–35.
