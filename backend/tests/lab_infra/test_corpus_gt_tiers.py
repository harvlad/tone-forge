"""Corpus manifest, GT normalization, deterministic tiers."""
from __future__ import annotations

from lab import config, corpus, gt, tiers


def test_manifest_schema_and_identity(fake_corpus, lab_env):
    assert len(fake_corpus) == 12  # 2 tracks * 3 stems * 2 splits
    row = fake_corpus.iloc[0]
    assert row["stem_id"].startswith("fakeslakh:Track")
    assert ":" in row["stem_id"] and "/" not in row["stem_id"]
    assert len(row["audio_hash"]) == 64
    assert not str(lab_env["dataset_root"]) in row["audio_path"], \
        "manifest paths must be relative to dataset root"
    assert row["inst_class_gm"] in ("Bass", "Piano", "Guitar")
    assert 0.9 < row["duration"] < 1.1
    assert row["sample_rate"] == 22050


def test_incremental_rebuild_reuses_hashes(fake_corpus, monkeypatch):
    calls = {"n": 0}
    import lab.corpus as corpus_mod
    real = corpus_mod.file_sha256

    def counting(path, **kw):
        calls["n"] += 1
        return real(path)
    monkeypatch.setattr(corpus_mod, "file_sha256", counting)
    df = corpus.build_manifest(datasets=["fakeslakh"], progress=False)
    assert calls["n"] == 0, "unchanged files must not be rehashed"
    assert len(df) == len(fake_corpus)


def test_gt_normalization_and_cache(fake_corpus):
    row = fake_corpus.iloc[0]
    notes = gt.get_gt_notes(corpus.resolve_midi(row), row["midi_hash"])
    assert list(notes.columns) == gt.GT_COLUMNS
    assert len(notes) == 2
    assert abs(notes.iloc[0]["onset"] - 0.10) < 0.01
    assert abs(notes.iloc[1]["onset"] - 0.60) < 0.01
    # cached file exists, content-addressed by midi_hash
    assert (config.GT_DIR / gt.GT_PARSER_VERSION / f"{row['midi_hash']}.parquet").exists()


def test_tier_selection_deterministic(fake_corpus):
    a = tiers.select_tier(fake_corpus, "sanity", inst_class="Bass",
                          dataset="fakeslakh", persist=False)
    b = tiers.select_tier(fake_corpus, "sanity", inst_class="Bass",
                          dataset="fakeslakh", persist=False)
    assert a["stem_id"].tolist() == b["stem_id"].tolist()
    assert len(a) <= config.TIER_SIZES["sanity"]
    assert (a["inst_class_gm"] == "Bass").all()
    assert (a["split"] == "validation").all()


def test_tier_seed_changes_selection_recorded(fake_corpus):
    a = tiers.select_tier(fake_corpus, "sanity", dataset="fakeslakh",
                          seed=1, persist=True)
    again = tiers.select_tier(fake_corpus, "sanity", dataset="fakeslakh",
                              seed=1, persist=True)
    assert a["stem_id"].tolist() == again["stem_id"].tolist()
    recs = list(config.TIERS_DIR.glob("*.json"))
    assert recs


def test_heldout_uses_test_split(fake_corpus):
    h = tiers.select_tier(fake_corpus, "heldout", dataset="fakeslakh", persist=False)
    assert (h["split"] == "test").all()


def test_validated100_shape(fake_corpus, monkeypatch):
    """validated100 = first N sorted validation stems, non-Unknown GM class."""
    monkeypatch.setitem(config.VALIDATED_REFERENCE, "n_stems", 4)
    df = fake_corpus.rename(columns={})
    df = df.copy()
    df.loc[:, "dataset"] = "slakh2100"  # pretend
    sel = tiers.validated100_stems(df)
    assert len(sel) == 4
    assert (sel["split"] == "validation").all()
    ordered = sel.sort_values(["track_id", "stem_name"])
    assert sel["stem_id"].tolist() == ordered["stem_id"].tolist()


def test_experiment_registry_roundtrip(lab_env):
    from lab import experiments
    eid = experiments.record("benchmark", models=["m"], n_stems=5,
                             result_summary={"global": {"recall": 0.5}})
    recs = experiments.load_all()
    assert recs[-1]["experiment_id"] == eid
    assert recs[-1]["git_commit"]
