"""Regression: every PipelineConfig preset must set ``stem_serve_url_base``.

The web-facing ``/api/analyze-url`` endpoint constructs its
``PipelineConfig`` via one of three factory presets (``fast``,
``standard``, ``deep``) based on the request's ``fast_mode`` /
``analysis_mode`` fields. The pipeline's ``_build_result`` then
wraps stem filesystem paths into web-fetchable URLs *only when*
``config.stem_serve_url_base`` is set — otherwise stems_paths
ships as raw ``/var/folders/...`` strings the browser cannot
fetch.

Prior to the fix only ``deep()`` set the base. ``fast()`` and
``standard()`` left it None, so any session analysed via
``/api/analyze-url`` with the default ``analysis_mode="studio"``
(which maps to ``standard()``) persisted stems as raw paths, and
the JAM client's ``fetch(stem.url)`` silently 404'd → no
playback.

This file pins all three presets to the same default so the JAM
playback path stays unbroken across every preset.
"""
from __future__ import annotations

from tone_forge.unified_pipeline import PipelineConfig


EXPECTED_STEM_URL_BASE = "/api/admin/serve-file"


def test_fast_preset_sets_stem_serve_url_base():
    config = PipelineConfig.fast()
    assert config.stem_serve_url_base == EXPECTED_STEM_URL_BASE


def test_standard_preset_sets_stem_serve_url_base():
    config = PipelineConfig.standard()
    assert config.stem_serve_url_base == EXPECTED_STEM_URL_BASE


def test_deep_preset_sets_stem_serve_url_base():
    config = PipelineConfig.deep()
    assert config.stem_serve_url_base == EXPECTED_STEM_URL_BASE


def test_url_base_is_shared_across_presets():
    """Defensive check: if anyone changes the default, all three
    move together. A diverging preset would silently regress JAM
    playback for just that mode (the exact failure pattern that
    motivated this test)."""
    bases = {
        PipelineConfig.fast().stem_serve_url_base,
        PipelineConfig.standard().stem_serve_url_base,
        PipelineConfig.deep().stem_serve_url_base,
    }
    assert len(bases) == 1, (
        f"Presets disagree on stem_serve_url_base: {bases}. "
        "JAM playback will break for whichever preset lacks the base."
    )
