"""Matcher parity with the validated implementation + metric sanity."""
from __future__ import annotations

from lab import matching


GT = [
    {"pitch": 60, "onset": 1.00, "offset": 1.50},
    {"pitch": 64, "onset": 2.00, "offset": 2.50},
    {"pitch": 67, "onset": 3.00, "offset": 3.50},
]


def test_exact_match_and_miss():
    pred = [
        {"pitch": 60, "onset": 1.05, "offset": 1.40},   # exact (within 100ms)
        {"pitch": 64, "onset": 2.50, "offset": 2.90},   # too late -> extra
    ]
    correct, octave, missed, extra = matching.match_notes(GT, pred)
    assert len(correct) == 1
    assert len(octave) == 0
    assert len(missed) == 2
    assert len(extra) == 1


def test_octave_bucket_not_counted_as_correct():
    pred = [{"pitch": 72, "onset": 1.02, "offset": 1.40}]  # +12 of GT 60
    correct, octave, missed, extra = matching.match_notes(GT, pred)
    assert len(correct) == 0
    assert len(octave) == 1
    assert octave[0][2] == 12
    m = matching.metrics_from_match_table(
        matching.match_table(_df(GT), _df(pred)))
    assert m["n_correct"] == 0
    assert m["n_octave"] == 1
    assert m["recall"] == 0.0  # validated formula: octave not in recall
    assert m["octave_error_rate"] == 1 / 3


def test_greedy_one_to_one():
    pred = [
        {"pitch": 60, "onset": 1.01, "offset": 1.2},
        {"pitch": 60, "onset": 1.02, "offset": 1.2},
    ]
    correct, octave, missed, extra = matching.match_notes(GT, pred)
    assert len(correct) == 1
    assert len(extra) == 1


def test_metric_formulas():
    m = matching.metrics_from_counts(n_gt=10, n_pred=8, n_correct=4, n_octave=2)
    assert m["recall"] == 0.4
    assert m["precision"] == 0.5
    assert abs(m["f1"] - 2 * 0.5 * 0.4 / 0.9) < 1e-12
    assert m["octave_error_rate"] == 0.2


def test_tolerance_changes_result_locally():
    pred = [{"pitch": 60, "onset": 1.08, "offset": 1.4}]
    c100, *_ = matching.match_notes(GT, pred, onset_tolerance=0.1)
    c50, *_ = matching.match_notes(GT, pred, onset_tolerance=0.05)
    assert len(c100) == 1 and len(c50) == 0


def test_match_cache_roundtrip(lab_env):
    import pandas as pd
    mt1 = matching.get_match_table("predkeyX", "gtidY", _df(GT), _df(
        [{"pitch": 60, "onset": 1.0, "offset": 1.4, "velocity": 1, "confidence": 0.5}]))
    mt2 = matching.get_match_table("predkeyX", "gtidY", _df(GT), _df(
        [{"pitch": 60, "onset": 1.0, "offset": 1.4, "velocity": 1, "confidence": 0.5}]))
    pd.testing.assert_frame_equal(mt1, mt2)
    k1 = matching.match_key("predkeyX", "gtidY", 0.1)
    k2 = matching.match_key("predkeyX", "gtidY", 0.05)
    assert k1 != k2


def _df(notes):
    import pandas as pd
    return pd.DataFrame(notes)


def test_match_table_respects_source_order():
    """Greedy matcher is order-sensitive; match_table must iterate
    predictions in adapter source order, not storage sort order."""
    from lab import schema
    gt_notes = [{"pitch": 60, "onset": 1.00, "offset": 1.50}]
    # source order: the pitch-72 (octave) note comes FIRST
    df = schema.normalize_predictions([
        {"pitch": 72, "onset": 1.02, "offset": 1.4},
        {"pitch": 60, "onset": 1.03, "offset": 1.4},
    ])
    # storage sort puts pitch 60 row before pitch 72 at different onsets;
    # force ambiguity: both within tolerance, source order must win
    mt = matching.match_table(_df(gt_notes), df)
    octave_rows = mt[mt["kind"] == "octave"]
    assert len(octave_rows) == 1, "source-order first note (octave) must match first"
    assert int(octave_rows.iloc[0]["pred_pitch"]) == 72


def test_oracle_sanity(fake_corpus, fake_adapter, monkeypatch):
    """Oracle recall >= each model's own recall."""
    from lab import analysis, runner
    import lab.adapters as adapters_mod
    stems = fake_corpus[fake_corpus["split"] == "validation"]

    class FakeB(fake_adapter):
        model_id = "fake_b"
        notes_to_return = [{"pitch": 41, "onset": 0.10, "offset": 0.4}]
    monkeypatch.setitem(adapters_mod.ADAPTERS, "fake_b", FakeB)

    for _, row in stems.iterrows():
        runner.get_predictions("fake_model", row)
        runner.get_predictions("fake_b", row)
    ra = analysis.evaluate_model("fake_model", stems)
    rb = analysis.evaluate_model("fake_b", stems)
    oracle = analysis.head_to_head("fake_model", "fake_b", stems)
    o = oracle["global"]["oracle_recall"]
    assert o >= ra["global"]["recall"] - 1e-9
    assert o >= rb["global"]["recall"] - 1e-9
