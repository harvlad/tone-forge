"""Phase 0D regression: deep-mode routing via select_pipeline_config.

Phase 0D root cause: ``UrlAnalyzeRequest.fast_mode`` defaults to True
and the analyze-url handler ladder checked ``fast_mode`` before
``analysis_mode``, so any client sending ``analysis_mode="deep"`` without
explicitly setting ``fast_mode=False`` got ``PipelineConfig.fast()``
(``separate_stems=False``) and Demucs was never invoked.

These tests pin the selection policy of the helper that replaced the
four ladder sites so the deep branch can no longer become unreachable
through a subsequent edit.

Goal: zero accidental behavior change outside deep mode.
"""

from tone_forge.unified_pipeline import (
    AnalysisMode,
    PipelineConfig,
    select_pipeline_config,
)


# ---------------------------------------------------------------------------
# Case 1: analysis_mode="deep" produces a deep PipelineConfig
# ---------------------------------------------------------------------------

def test_deep_mode_wins_over_default_fast_mode():
    """analysis_mode='deep' MUST reach deep() even when fast_mode is True.

    This is the Phase 0D regression: pre-fix, fast_mode=True
    short-circuited the ladder and the deep branch was unreachable.
    """
    config = select_pipeline_config(analysis_mode="deep", fast_mode=True)

    assert config.mode == AnalysisMode.DEEP
    assert config.separate_stems is True
    assert config.force_stem_separation is True


def test_deep_mode_with_explicit_fast_mode_false():
    """Pre-fix legacy path: fast_mode=False + analysis_mode=deep -> deep()."""
    config = select_pipeline_config(analysis_mode="deep", fast_mode=False)

    assert config.mode == AnalysisMode.DEEP
    assert config.separate_stems is True
    assert config.force_stem_separation is True


def test_deep_mode_case_insensitive():
    """analysis_mode comparison must tolerate mixed case."""
    for variant in ("DEEP", "Deep", " deep ", "deep"):
        config = select_pipeline_config(analysis_mode=variant, fast_mode=True)
        assert config.mode == AnalysisMode.DEEP, f"failed for {variant!r}"
        assert config.separate_stems is True
        assert config.force_stem_separation is True


# ---------------------------------------------------------------------------
# Case 2: explicit fast intent still maps to fast()
# ---------------------------------------------------------------------------

def test_quick_mode_maps_to_fast():
    """analysis_mode='quick' is the legacy spelling for fast()."""
    config = select_pipeline_config(analysis_mode="quick", fast_mode=True)

    assert config.mode == AnalysisMode.FAST
    assert config.separate_stems is False


def test_fast_alias_maps_to_fast():
    """analysis_mode='fast' is an accepted alias for the fast() factory."""
    config = select_pipeline_config(analysis_mode="fast", fast_mode=True)

    assert config.mode == AnalysisMode.FAST
    assert config.separate_stems is False


def test_quick_mode_with_fast_mode_false_still_fast():
    """Quick wins over fast_mode=False (explicit mode beats implicit flag)."""
    config = select_pipeline_config(analysis_mode="quick", fast_mode=False)

    assert config.mode == AnalysisMode.FAST
    assert config.separate_stems is False


# ---------------------------------------------------------------------------
# Case 3: legacy requests without analysis_mode behave exactly as before
# ---------------------------------------------------------------------------

def test_legacy_default_fast_mode_true_returns_fast():
    """Legacy: no analysis_mode, fast_mode=True (the default) -> fast()."""
    # 'studio' is the schema default for analysis_mode.
    config = select_pipeline_config(analysis_mode="studio", fast_mode=True)

    assert config.mode == AnalysisMode.FAST
    assert config.separate_stems is False


def test_legacy_no_analysis_mode_with_fast_mode_false_returns_standard():
    """Legacy: fast_mode=False, default analysis_mode -> standard()."""
    config = select_pipeline_config(analysis_mode="studio", fast_mode=False)

    assert config.mode == AnalysisMode.STANDARD
    assert config.separate_stems is True
    # standard() does NOT force separation; the detection gate still applies.
    assert config.force_stem_separation is False


def test_legacy_unknown_mode_falls_through_to_standard_or_fast():
    """Unknown analysis_mode strings behave like the legacy default.

    Pre-fix, an unknown analysis_mode with fast_mode=True went to
    fast(). Post-fix, that path must be preserved.
    """
    # Unknown + fast_mode=True -> fast() (rule 3 of select policy).
    fast_cfg = select_pipeline_config(
        analysis_mode="balanced", fast_mode=True
    )
    assert fast_cfg.mode == AnalysisMode.FAST

    # Unknown + fast_mode=False -> standard() (rule 4 of select policy).
    std_cfg = select_pipeline_config(
        analysis_mode="balanced", fast_mode=False
    )
    assert std_cfg.mode == AnalysisMode.STANDARD


def test_none_analysis_mode_with_fast_mode_true_returns_fast():
    """A None analysis_mode is treated as 'no opinion'; fast_mode wins."""
    config = select_pipeline_config(analysis_mode=None, fast_mode=True)

    assert config.mode == AnalysisMode.FAST
    assert config.separate_stems is False


def test_empty_analysis_mode_with_fast_mode_false_returns_standard():
    """Empty analysis_mode + fast_mode=False -> standard()."""
    config = select_pipeline_config(analysis_mode="", fast_mode=False)

    assert config.mode == AnalysisMode.STANDARD


# ---------------------------------------------------------------------------
# Schema invariants: the factories themselves keep their separate_stems
# contracts, so the helper is correct by composition.
# ---------------------------------------------------------------------------

def test_pipeline_config_fast_has_no_stem_separation():
    """Invariant: PipelineConfig.fast() never separates stems."""
    cfg = PipelineConfig.fast()
    assert cfg.separate_stems is False
    assert cfg.force_stem_separation is False


def test_pipeline_config_standard_separates_full_mix_only():
    """Invariant: PipelineConfig.standard() separates only full mixes."""
    cfg = PipelineConfig.standard()
    assert cfg.separate_stems is True
    assert cfg.force_stem_separation is False


def test_pipeline_config_deep_always_separates():
    """Invariant: PipelineConfig.deep() forces separation."""
    cfg = PipelineConfig.deep()
    assert cfg.separate_stems is True
    assert cfg.force_stem_separation is True
