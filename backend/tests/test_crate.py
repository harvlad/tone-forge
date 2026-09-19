"""Vinyl Crate — contract integrity, license doctrine, match ranking, faceted
search, ingestion blind-gate, and the three API routes.

The crate REUSES the borrow engine (a crate track's stored analysis is adapted
into a borrow entry and ranked/rendered by the same code), so these tests pin
the crate-specific layer: the DTOs + license record, the weighted match model,
the faceted search, and the endpoints — without a live pull (a committed seed
crate + synthetic fixtures stand in).
"""
from __future__ import annotations

import json

import pytest

from tone_forge.contracts import (
    CrateFeatures,
    CrateLicense,
    CrateLicenseRecord,
    CrateTrack,
)
from tone_forge.crate import ingest as cingest
from tone_forge.crate import match as cmatch
from tone_forge.crate import registry as creg
from tone_forge.crate import search as csearch


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _lic(license_id=CrateLicense.CC_BY, *, source="jamendo", tid="t",
         attribution="“X” by Y (CC BY 4.0), https://ex/x"):
    return CrateLicenseRecord(
        license_id=license_id,
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution=attribution,
        source=source,
        source_track_id=tid,
        source_url="https://example.org/track",
        content_hash="deadbeef",
        acquired_at="2026-09-14T00:00:00+00:00",
        export_encumbered=creg.encumbered_for(license_id),
    )


def _feat(tempo=120.0, key="C major", *, key_conf=0.7, stems=("drums", "bass", "other"),
          energy=0.5, pc=(), duration=90.0, mel_reg=None, mel_conf=0.0,
          has_vocals=False):
    return CrateFeatures(
        tempo_bpm=tempo, tempo_confidence=0.5, detected_key=key,
        key_confidence=key_conf, duration_s=duration, section_count=4,
        energy=energy, available_stems=tuple(stems), pc_histogram=tuple(pc),
        melody_register=mel_reg, melody_confidence=mel_conf, has_vocals=has_vocals,
    )


def _track(tid, tempo=120.0, key="C major", *, genre="", tags=(), mood="",
           title="Track", artist="Artist", license_id=CrateLicense.CC_BY,
           stems=("drums", "bass", "other"), energy=0.5, duration=90.0,
           mel_reg=None, mel_conf=0.0, has_vocals=False):
    return CrateTrack(
        id=tid, title=title, artist=artist,
        license=_lic(license_id, tid=tid),
        features=_feat(tempo, key, stems=stems, energy=energy, duration=duration,
                       mel_reg=mel_reg, mel_conf=mel_conf, has_vocals=has_vocals),
        genre=genre, tags=tuple(tags), mood=mood,
    )


def _blob(tempo=120.0, key="C major", chords=("C", "G", "Am", "F"),
          stems=("drums", "bass", "other"), melody=None, key_strength=0.7):
    r = {
        "tempo_bpm": tempo, "detected_key": key, "detected_key_strength": key_strength,
        "downbeats_s": [float(i) for i in range(9)], "duration_sec": 90.0,
        "chords": [{"symbol": s, "start_s": i, "end_s": i + 1}
                   for i, s in enumerate(chords)],
        "stems_paths": {s: f"crate/x/{s}.wav" for s in stems},
        "sections": [{"type": "verse", "start_time": 0.0, "end_time": 8.0, "energy": 0.5}],
        "performance_graph": {"assets": [{"id": "a1"}], "phrases": [{"energy": 0.5}]},
    }
    if melody is not None:
        r["melody"] = {"source_stem": "other", "confidence": 0.7, "notes": melody}
    return r


# ---------------------------------------------------------------------------
# Contract integrity + license doctrine
# ---------------------------------------------------------------------------

def test_export_encumbered_true_only_for_by_sa():
    assert creg.encumbered_for(CrateLicense.CC_BY_SA) is True
    assert creg.encumbered_for(CrateLicense.CC_BY) is False
    assert creg.encumbered_for(CrateLicense.CC0) is False
    # The record built by ingest carries the resolved boolean (never re-derived).
    rec = cingest.build_license_record({
        "license": "CC-BY-SA-4.0", "source": "ccmixter", "id": "z",
        "source_url": "http://ccmixter.org/x", "attribution": "a"})
    assert rec.license_id == CrateLicense.CC_BY_SA
    assert rec.export_encumbered is True
    cc0 = cingest.build_license_record({
        "license": "CC0", "source": "fma", "id": "z",
        "source_url": "https://fma/x", "attribution": "a"})
    assert cc0.export_encumbered is False


def test_license_preference_order():
    assert creg.license_rank(CrateLicense.CC0) < creg.license_rank(CrateLicense.CC_BY)
    assert creg.license_rank(CrateLicense.CC_BY) < creg.license_rank(CrateLicense.CC_BY_SA)


def test_license_alias_parsing():
    # The loose catalog.json "CC-BY" alias and the enum value both resolve.
    assert creg._parse_license("CC-BY") == CrateLicense.CC_BY
    assert creg._parse_license("CC-BY-4.0") == CrateLicense.CC_BY
    assert creg._parse_license("CC-BY-SA") == CrateLicense.CC_BY_SA
    assert creg._parse_license("CC0") == CrateLicense.CC0


def test_license_version_agnostic_parsing():
    # The crate seed manifest carries real CC versions (ccMixter is mostly
    # CC-BY-3.0 with one CC-BY-2.5; Kevin MacLeod is CC-BY-4.0). Every
    # attribution-only spelling — regardless of version or spacing — collapses
    # to the coarse CC_BY the model uses.
    for raw in ("CC-BY-3.0", "CC-BY-2.5", "CC-BY-2.0", "CC BY 3.0", "cc-by-3.0"):
        assert creg._parse_license(raw) == CrateLicense.CC_BY, raw
    # ShareAlike is matched at ANY version and BEFORE plain attribution, so a
    # versioned SA is never misfiled as unencumbered CC-BY (which would let a
    # copyleft track ride a "clean export").
    for raw in ("CC-BY-SA-3.0", "CC-BY-SA-2.5", "CC-BY-SA-4.0"):
        assert creg._parse_license(raw) == CrateLicense.CC_BY_SA, raw
    # CC0 tolerates a trailing version too.
    assert creg._parse_license("CC0-1.0") == CrateLicense.CC0
    # And the encumbrance derivation follows the resolved id, not the spelling.
    assert creg.encumbered_for(creg._parse_license("CC-BY-3.0")) is False
    assert creg.encumbered_for(creg._parse_license("CC-BY-SA-3.0")) is True


def test_track_round_trips_through_dict():
    t = _track("crate:jamendo:9", genre="funk", tags=("groovy", "horns"),
               license_id=CrateLicense.CC_BY_SA)
    d = creg.track_to_dict(t)
    # The serving convenience mirrors ride along (attribution is a CC-BY must).
    assert d["attribution"] == t.license.attribution
    assert d["licenseId"] == "CC-BY-SA-4.0"
    assert d["exportEncumbered"] is True
    back = creg.track_from_dict(d)
    assert back.id == t.id
    assert back.license.license_id == CrateLicense.CC_BY_SA
    assert back.license.export_encumbered is True
    assert back.features.tempo_bpm == t.features.tempo_bpm
    assert back.tags == ("groovy", "horns")
    assert back.features.time_signature == (4, 4)


# ---------------------------------------------------------------------------
# Match ranking
# ---------------------------------------------------------------------------

def test_same_key_same_tempo_outranks_distant():
    session = _blob(120, "C major")
    exact = _track("exact", 120, "C major")
    distant = _track("distant", 138, "G major")
    blobs = {"exact": _blob(120, "C major"),
             "distant": _blob(138, "G major", chords=("G", "D", "Em", "C"))}
    ranked = cmatch.rank_crate(session, [distant, exact], blobs, stem="other")
    assert [r["trackId"] for r in ranked][0] == "exact"
    assert ranked[0]["matchScore"] >= ranked[1]["matchScore"]


def test_tritone_clash_is_vetoed_for_melodic_stem():
    session = _blob(120, "C major")
    clash = _track("clash", 120, "F# major")
    blobs = {"clash": _blob(120, "F# major", chords=("F#", "C#", "D#m", "B"))}
    ranked = cmatch.rank_crate(session, [clash], blobs, stem="other")
    assert ranked == []  # harmonic floor gate drops it entirely


def test_genre_affinity_tiebreak():
    # Two otherwise identical tracks; the session genre picks the winner.
    session = _blob(120, "C major")
    session["genre"] = "electronic"
    session["tags"] = ["retro", "synth"]
    same_genre = _track("elec", 120, "C major", genre="electronic", tags=("retro",))
    off_genre = _track("folk", 120, "C major", genre="folk", tags=("acoustic",))
    blobs = {"elec": _blob(120, "C major"), "folk": _blob(120, "C major")}
    ranked = cmatch.rank_crate(session, [off_genre, same_genre], blobs, stem="other")
    assert ranked[0]["trackId"] == "elec"
    # CONTRAST mode flips it — same code path, a toggle not a branch.
    ranked_c = cmatch.rank_crate(session, [off_genre, same_genre], blobs,
                                 stem="other", genre_mode="contrast")
    assert ranked_c[0]["trackId"] == "folk"


def test_clean_export_filter_vetoes_by_sa():
    session = _blob(120, "C major")
    sa = _track("sa", 120, "C major", license_id=CrateLicense.CC_BY_SA)
    blobs = {"sa": _blob(120, "C major")}
    assert cmatch.rank_crate(session, [sa], blobs, stem="other", clean_export=True) == []
    # Without the filter it is offered (badged, not excluded).
    assert cmatch.rank_crate(session, [sa], blobs, stem="other", clean_export=False)


def test_tempo_out_of_band_vetoed():
    session = _blob(120, "C major")
    # 200 BPM vs 120: raw 0.6, folds to 1.2 (m=0.5) → dist 0.2 within band → kept.
    ok = _track("ok", 200, "C major")
    # 170 BPM: 120/170=0.706 → *1=.294; *2=1.41; *0.5=.353; best .294 within .5 → kept.
    # 300 BPM: 120/300=0.4, folded best |0.4*2-1|=0.2 within → kept. Use an
    # unfoldable extreme instead: session 120, donor 121 → fine; donor 175 →
    # 120/175=0.686, folds *1 dist .314 ok. Force a veto with donor 155 vs 120:
    far = _track("far", 155, "C major")  # 120/155=0.774 → *1 .226 ok too...
    blobs = {"ok": _blob(200, "C major"), "far": _blob(155, "C major")}
    r = cmatch.rank_crate(session, [ok, far], blobs, stem="other")
    assert {c["trackId"] for c in r} == {"ok", "far"}  # both foldable
    # A genuinely out-of-band donor (session 120, donor 300 is foldable; use
    # 120 vs 121*? Instead assert the gate math directly on _fold): 120 vs 400.
    huge = _track("huge", 400, "C major")
    r2 = cmatch.rank_crate(session, [huge], {"huge": _blob(400, "C major")}, stem="other")
    # 120/400=0.3 → best fold |0.3*2-1|=0.4 < 0.5 → still kept; go past 4x:
    insane = _track("insane", 700, "C major")
    r3 = cmatch.rank_crate(session, [insane], {"insane": _blob(700, "C major")}, stem="other")
    assert r3 == []  # 120/700 ratio < 0.25 folded distance > limit → vetoed


def test_instrumentation_complement_rewards_missing_stem():
    # Drum-less session (otherwise full); a strong-drum track fills the ONE hole
    # → complement 1.0. (A session lacking two core stems would score the
    # fraction filled; real demucs sessions carry all four, so a single genuine
    # gap is the realistic complementarity case.)
    session = _blob(120, "C major", stems=("bass", "other", "vocals"))
    drummer = _track("drummer", 120, "C major", stems=("drums", "other"))
    blobs = {"drummer": _blob(120, "C major", stems=("drums", "other"))}
    ranked = cmatch.rank_crate(session, [drummer], blobs, stem="other")
    assert ranked and ranked[0]["signals"]["instr"] == pytest.approx(1.0)


def test_melody_fit_participates_when_both_have_melody():
    s_notes = [{"pitch": 60 + p, "start": i * 0.5, "end": i * 0.5 + 0.4, "velocity": 90}
               for i, p in enumerate([0, 2, 4, 2, 0])]
    session = _blob(120, "C major", melody=s_notes)
    # A track whose melody sits in-key and in-register.
    t_notes = [{"pitch": 60 + p, "start": i * 0.5, "end": i * 0.5 + 0.4, "velocity": 88}
               for i, p in enumerate([0, 2, 4, 2, 0])]
    track = _track("mel", 120, "C major", mel_reg=64, mel_conf=0.8)
    blobs = {"mel": _blob(120, "C major", melody=t_notes)}
    ranked = cmatch.rank_crate(session, [track], blobs, stem="other")
    assert "melody" in ranked[0]["signals"]
    assert ranked[0]["signals"]["melody"] >= 0.5


def test_score_reports_transpose_and_stretch():
    session = _blob(120, "G minor")
    donor = _track("d", 120, "C major")  # C→G is -5 (shortest)
    sc = cmatch.score_crate(session, donor, _blob(120, "C major"), stem="other")
    assert sc["transposeSemis"] == -5
    assert sc["stretchRatio"] == pytest.approx(1.0)
    assert 0.0 <= sc["matchScore"] <= 1.0


def test_blank_canvas_ranks_without_session():
    # No session: only session-independent signals (instrumentation) rank.
    full = _track("full", 120, "C major", stems=("drums", "bass", "other", "vocals"))
    thin = _track("thin", 120, "C major", stems=("other",))
    ranked = cmatch.rank_crate(None, [thin, full], {}, stem="other")
    assert [r["trackId"] for r in ranked] == ["full", "thin"]


def test_stem_unavailable_is_vetoed():
    session = _blob(120, "C major")
    no_bass = _track("nb", 120, "C major", stems=("drums", "other"))
    assert cmatch.rank_crate(session, [no_bass], {}, stem="bass") == []


# ---------------------------------------------------------------------------
# Faceted search
# ---------------------------------------------------------------------------

def _search_set():
    return [
        _track("a", 120, "C major", genre="funk", tags=("groovy",), mood="happy",
               stems=("drums", "bass", "other", "vocals"), duration=100.0,
               has_vocals=True),
        _track("b", 90, "A minor", genre="jazz", tags=("smooth", "mellow"),
               mood="chill", stems=("drums", "bass"), duration=200.0,
               license_id=CrateLicense.CC0),
        _track("c", 140, "E minor", genre="funk", tags=("groovy", "horns"),
               mood="happy", stems=("drums", "other", "vocals"), duration=150.0,
               license_id=CrateLicense.CC_BY_SA),
    ]


def test_search_genre_facet():
    res = csearch.search_crate(_search_set(), genre="funk")
    assert {t.id for t in res["tracks"]} == {"a", "c"}
    assert res["total"] == 2


def test_search_tempo_range_and_key():
    res = csearch.search_crate(_search_set(), tempo_min=100, tempo_max=145)
    assert {t.id for t in res["tracks"]} == {"a", "c"}
    res2 = csearch.search_crate(_search_set(), key="A minor")
    assert {t.id for t in res2["tracks"]} == {"b"}


def test_search_camelot_neighbourhood():
    # C major = 8B; A minor = 8A (relative) is in 8B's neighbourhood.
    assert csearch.key_to_camelot("C major") == "8B"
    assert csearch.key_to_camelot("A minor") == "8A"
    res = csearch.search_crate(_search_set(), camelot="8B")
    ids = {t.id for t in res["tracks"]}
    assert "a" in ids and "b" in ids   # C major + its relative A minor
    assert "c" not in ids              # E minor (9A) is not adjacent to 8B


def test_search_stems_and_vocals_and_license():
    res = csearch.search_crate(_search_set(), stems=["vocals"])
    assert {t.id for t in res["tracks"]} == {"a", "c"}
    res2 = csearch.search_crate(_search_set(), has_vocals=True)
    assert {t.id for t in res2["tracks"]} == {"a"}
    res3 = csearch.search_crate(_search_set(), clean_export=True)
    assert "c" not in {t.id for t in res3["tracks"]}   # CC-BY-SA excluded
    res4 = csearch.search_crate(_search_set(), license_ids=["CC0"])
    assert {t.id for t in res4["tracks"]} == {"b"}


def test_search_free_text_and_facet_counts():
    tracks = _search_set()
    tracks[0] = _track("a", 120, "C major", genre="funk", title="Midnight Groove",
                       artist="The Cats", tags=("groovy",), mood="happy")
    res = csearch.search_crate(tracks, q="midnight")
    assert [t.id for t in res["tracks"]] == ["a"]
    counts = csearch.search_crate(tracks)["facetCounts"]
    assert counts["genre"]["funk"] == 2
    assert counts["mood"]["happy"] >= 1
    assert "8B" in counts["camelot"] or "8A" in counts["camelot"]


def test_search_sort_and_pagination():
    res = csearch.search_crate(_search_set(), sort="tempo")
    assert [t.features.tempo_bpm for t in res["tracks"]] == [90.0, 120.0, 140.0]
    page = csearch.search_crate(_search_set(), sort="tempo", limit=1, offset=1)
    assert [t.id for t in page["tracks"]] == ["a"]
    assert page["total"] == 3   # total is pre-pagination


# ---------------------------------------------------------------------------
# Ingestion — admission, features, blind gate, end-to-end
# ---------------------------------------------------------------------------

def test_admission_rejects_cambridge_and_incomplete_license():
    with pytest.raises(cingest.IngestError):
        cingest.validate_admission({
            "source": "cambridge-mt", "id": "x", "title": "t", "artist": "a",
            "source_url": "u", "license_url": "l", "attribution": "att"})
    with pytest.raises(cingest.IngestError):
        cingest.validate_admission({
            "source": "jamendo", "id": "x", "title": "t", "artist": "a",
            "source_url": "u", "license_url": "l"})  # missing attribution
    # A complete Jamendo row passes.
    cingest.validate_admission({
        "source": "jamendo", "id": "x", "title": "t", "artist": "a",
        "source_url": "u", "license_url": "l", "attribution": "att"})


def test_features_from_result_projection():
    mel = [{"pitch": 60, "start": 0, "end": 1, "velocity": 90},
           {"pitch": 64, "start": 1, "end": 2, "velocity": 90},
           {"pitch": 67, "start": 2, "end": 3, "velocity": 90}]
    r = _blob(126, "D minor", stems=("drums", "bass", "other", "vocals"), melody=mel)
    f = cingest.features_from_result(r)
    assert f.tempo_bpm == 126
    assert f.detected_key == "D minor"
    assert f.key_confidence == pytest.approx(0.7)
    assert set(f.available_stems) == {"drums", "bass", "other", "vocals"}
    assert f.has_vocals is True
    assert f.section_count == 1
    assert len(f.pc_histogram) == 12          # folded from the chord ribbon
    assert f.melody_register == 64            # median of 60/64/67
    assert f.melody_confidence == pytest.approx(0.7)


def test_graph_gate():
    assert cingest.graph_is_present(_blob()) is True
    no_graph = _blob()
    no_graph.pop("performance_graph")
    assert cingest.graph_is_present(no_graph) is False


def test_default_blind_gate(monkeypatch):
    # Real gate: graph present AND the curated kit yields pads. serve.kit_payload
    # is stubbed (as the borrow tests do) so no GPU/stems are needed.
    monkeypatch.setattr("tone_forge.performance.serve.kit_payload",
                        lambda sid, res, pads=12: {"pads": [{"name": "p"}]})
    assert cingest.default_blind_gate("t", _blob()) is True
    monkeypatch.setattr("tone_forge.performance.serve.kit_payload",
                        lambda sid, res, pads=12: {"pads": []})
    assert cingest.default_blind_gate("t", _blob()) is False   # empty kit → drop
    # No graph → dropped before serve is even consulted.
    ng = _blob(); ng.pop("performance_graph")
    assert cingest.default_blind_gate("t", ng) is False


def test_ingest_track_end_to_end(tmp_path, monkeypatch):
    crate_dir = tmp_path / "crate"
    monkeypatch.setenv("TONEFORGE_CRATE_DIR", str(crate_dir))
    creg.invalidate_cache()

    def fake_download(url, staging):
        staging.mkdir(parents=True, exist_ok=True)
        p = staging / "clip.wav"
        p.write_bytes(b"RIFFfake")
        return p

    result = _blob(128, "C major", stems=("drums", "bass", "other"))

    meta = {
        "id": "crate:jamendo:42", "title": "Test", "artist": "Tester",
        "source": "jamendo", "source_track_id": "42",
        "source_url": "https://jamendo.com/42",
        "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "“Test” by Tester (CC BY 4.0), https://jamendo.com/42",
        "genre": "pop", "tags": ["bright"], "mood": "happy",
    }

    track = cingest.ingest_track(
        meta, analyzer=lambda p: result, downloader=fake_download,
        blind_gate=lambda tid, res: True)

    # Sidecars + analysis blob + manifest row written; content hash filled.
    assert (crate_dir / "licenses" / "crate_jamendo_42.json").is_file()
    assert (crate_dir / "analysis" / "crate_jamendo_42.json").is_file()
    assert track.license.content_hash and len(track.license.content_hash) == 64
    assert track.graph_available is True
    assert track.features.tempo_bpm == 128

    creg.invalidate_cache()
    loaded = creg.load_crate()
    assert any(t.id == "crate:jamendo:42" for t in loaded)
    # to_borrow_entry hands the stored analysis to the borrow ranker.
    entry = creg.to_borrow_entry(track)
    assert entry["id"] == "crate:jamendo:42"
    assert entry["result"]["detected_key"] == "C major"


def _load_ingest_cli():
    """Import the wiring script (scripts/ingest_crate.py) by path — it isn't a
    package module. Safe at import time: its heavy analysis deps (torch/demucs)
    are imported lazily inside _make_analyzer, not at module load."""
    import importlib.util
    from pathlib import Path as _P
    script = _P(__file__).resolve().parents[1] / "scripts" / "ingest_crate.py"
    spec = importlib.util.spec_from_file_location("ingest_crate_cli", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_fleet_driver():
    """Import the fleet driver (scripts/crate_fleet.py) by path. Its only heavy
    import (r2_storage) is lazy inside merge_and_install, so module load is safe
    and offline."""
    import importlib.util
    from pathlib import Path as _P
    script = _P(__file__).resolve().parents[1] / "scripts" / "crate_fleet.py"
    spec = importlib.util.spec_from_file_location("crate_fleet_driver", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fleet_resolve_image_prefers_envfile(monkeypatch):
    # The last fleet run paid the ~20 min cold install because pods booted the
    # generic base even though RUNPOD_IMAGE was set. resolve_image must take the
    # .env value first, then the process env, then the generic fallback.
    fleet = _load_fleet_driver()
    monkeypatch.delenv("RUNPOD_IMAGE", raising=False)
    monkeypatch.delenv("CRATE_RUNPOD_IMAGE", raising=False)

    # 1) .env value wins.
    assert fleet.resolve_image({"RUNPOD_IMAGE": "prod/analysis:latest"}) == "prod/analysis:latest"
    # 2) process env is honoured when the file omits it (exported-but-unwritten).
    monkeypatch.setenv("RUNPOD_IMAGE", "shell/exported:1")
    assert fleet.resolve_image({}) == "shell/exported:1"
    # 3) neither set → the documented generic fallback (still works, just slow).
    monkeypatch.delenv("RUNPOD_IMAGE", raising=False)
    assert fleet.resolve_image({}) == fleet.IMAGE_DEFAULT


def test_fleet_crate_image_override_beats_global_runpod_image(monkeypatch):
    # CRATE_RUNPOD_IMAGE is a crate-SCOPED override so the fleet can run on the
    # baked worker image without touching the global RUNPOD_IMAGE that the prod
    # analysis autoscaler also reads. It must win over RUNPOD_IMAGE from both
    # the process env and the .env dict.
    fleet = _load_fleet_driver()
    monkeypatch.setenv("CRATE_RUNPOD_IMAGE", "ghcr.io/harvlad/tone-forge-worker:latest")
    monkeypatch.setenv("RUNPOD_IMAGE", "shell/exported:1")
    assert (fleet.resolve_image({"RUNPOD_IMAGE": "prod/analysis:latest"})
            == "ghcr.io/harvlad/tone-forge-worker:latest")
    # .env-level CRATE_RUNPOD_IMAGE also wins over a global RUNPOD_IMAGE.
    monkeypatch.delenv("CRATE_RUNPOD_IMAGE", raising=False)
    assert (fleet.resolve_image({"CRATE_RUNPOD_IMAGE": "crate/baked:1",
                                 "RUNPOD_IMAGE": "prod/analysis:latest"})
            == "crate/baked:1")


def test_shard_selector_round_robin():
    cli = _load_ingest_cli()
    metas = [{"id": str(i)} for i in range(10)]
    s0 = cli._select_shard(metas, "0/4")
    s3 = cli._select_shard(metas, "3/4")
    assert [m["id"] for m in s0] == ["0", "4", "8"]
    assert [m["id"] for m in s3] == ["3", "7"]
    # Union of all shards == the whole manifest, no track dropped or duplicated.
    allsh = []
    for i in range(4):
        allsh += cli._select_shard(metas, f"{i}/4")
    assert sorted(int(m["id"]) for m in allsh) == list(range(10))
    assert cli._select_shard(metas, None) == metas


def test_run_batch_concurrency_isolates_failures(tmp_path, monkeypatch):
    # Concurrency path: N tracks analyzed at once; one track's download raises →
    # it is rejected, the others are still admitted, counts are correct, and the
    # shared registry survives the concurrent writes (all good ids present).
    cli = _load_ingest_cli()
    crate_dir = tmp_path / "crate"
    monkeypatch.setenv("TONEFORGE_CRATE_DIR", str(crate_dir))
    creg.invalidate_cache()

    def fake_download(url, staging):
        if "FAIL" in url:
            raise IOError("simulated 403 from the source CDN")
        staging.mkdir(parents=True, exist_ok=True)
        p = staging / (url.rsplit("/", 1)[-1] or "c.wav")
        p.write_bytes(b"RIFFfake")
        return p

    def make_meta(i, fail=False):
        return {
            "id": f"crate:jamendo:{i}", "title": f"T{i}", "artist": "A",
            "source": "jamendo", "source_track_id": str(i),
            "source_url": f"https://jamendo.com/{i}",
            "download_url": ("https://x/FAIL.mp3" if fail else f"https://x/{i}.mp3"),
            "license_id": "CC-BY-3.0",
            "license_url": "https://creativecommons.org/licenses/by/3.0/",
            "attribution": f"“T{i}” by A (CC-BY-3.0)",
        }

    metas = [make_meta(i, fail=(i == 2)) for i in range(6)]
    ok, fail = cli.run_batch(
        metas, analyzer=lambda p: _blob(120, "C major"),
        downloader=fake_download, concurrency=4, crate_dir=crate_dir,
        blind_gate=lambda tid, res: True)

    assert (ok, fail) == (5, 1)
    creg.invalidate_cache()
    loaded_ids = {t.id for t in creg.load_crate()}
    assert loaded_ids == {f"crate:jamendo:{i}" for i in range(6) if i != 2}
    assert "crate:jamendo:2" not in loaded_ids   # the failed download stayed out


def test_ingest_rejects_failed_blind_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("TONEFORGE_CRATE_DIR", str(tmp_path / "crate"))
    creg.invalidate_cache()
    meta = {
        "id": "crate:fma:9", "title": "Wash", "artist": "Noise",
        "source": "fma", "source_track_id": "9", "source_url": "https://fma/9",
        "license": "CC0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution": "“Wash” by Noise (CC0)"}

    def fake_download(url, staging):
        staging.mkdir(parents=True, exist_ok=True)
        p = staging / "c.wav"
        p.write_bytes(b"x")
        return p

    with pytest.raises(cingest.IngestError):
        cingest.ingest_track(
            meta, analyzer=lambda p: _blob(),
            downloader=fake_download,
            blind_gate=lambda tid, res: False)


# ---------------------------------------------------------------------------
# Committed seed crate — parses, license-complete, encumbrance correct
# ---------------------------------------------------------------------------

def test_seed_crate_parses_and_is_license_complete(monkeypatch):
    monkeypatch.delenv("TONEFORGE_CRATE_DIR", raising=False)
    creg.invalidate_cache()
    tracks = creg.load_crate()
    assert len(tracks) >= 3
    by_id = {t.id: t for t in tracks}
    # Every seed track carries complete, displayable attribution (CC-BY must).
    for t in tracks:
        assert t.license.attribution.strip()
        assert t.license.license_url.startswith("https://creativecommons.org/")
        assert t.license.source in cingest.SOURCE_ALLOWLIST
        assert t.graph_available is True
    # Encumbrance is set correctly per license.
    assert by_id["crate:ccmixter:0003"].license.export_encumbered is True
    assert by_id["crate:fma:0001"].license.export_encumbered is False
    assert by_id["crate:jamendo:0002"].license.license_id == CrateLicense.CC_BY
    # The license sidecar is the source of truth and matches the manifest.
    rec = creg.license_record("crate:fma:0001")
    assert rec is not None and rec.license_id == CrateLicense.CC0


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

import tone_forge_api  # noqa: E402
from tone_forge.performance import borrow as _borrow  # noqa: E402

_client = TestClient(tone_forge_api.app)


def test_api_search_returns_seed_with_attribution(monkeypatch):
    monkeypatch.delenv("TONEFORGE_CRATE_DIR", raising=False)
    creg.invalidate_cache()
    r = _client.get("/api/crate/search", params={"genre": "electronic"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    row = body["tracks"][0]
    assert row["attribution"]                    # CC-BY obligation on every row
    assert "facetCounts" in body
    # Free-text + clean-export facet.
    r2 = _client.get("/api/crate/search", params={"q": "sunset", "clean_export": "true"})
    ids = {t["id"] for t in r2.json()["tracks"]}
    assert "crate:jamendo:0002" in ids
    assert "crate:ccmixter:0003" not in ids      # BY-SA excluded


def test_api_candidates_blank_canvas(monkeypatch):
    monkeypatch.delenv("TONEFORGE_CRATE_DIR", raising=False)
    creg.invalidate_cache()
    r = _client.get("/api/crate/candidates", params={"stem": "other", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    cands = body["candidates"]
    assert cands, "blank-canvas candidates should rank on session-independent signals"
    # Superset of the borrow-candidates shape.
    top = cands[0]
    for key in ("entryId", "name", "score", "donorStem", "matchScore", "attribution"):
        assert key in top
    # jamendo has all four core stems → best instrumentation complement.
    assert top["entryId"] == "crate:jamendo:0002"


def test_api_candidates_with_session(monkeypatch):
    monkeypatch.delenv("TONEFORGE_CRATE_DIR", raising=False)
    creg.invalidate_cache()
    session = {"id": "sess", "name": "My Song", "result": _blob(120, "A minor")}
    monkeypatch.setattr(tone_forge_api, "_get_history_item",
                        lambda eid: session if eid == "sess" else None)
    r = _client.get("/api/crate/candidates",
                    params={"session_id": "sess", "stem": "other"})
    assert r.status_code == 200
    body = r.json()
    assert body["targetTempo"] == 120
    assert body["targetKey"] == "A minor"
    assert body["candidates"]
    for c in body["candidates"]:
        assert 0.0 <= c["matchScore"] <= 1.0


def test_api_crate_borrow_stamps_attribution(monkeypatch):
    monkeypatch.delenv("TONEFORGE_CRATE_DIR", raising=False)
    creg.invalidate_cache()
    monkeypatch.setattr(tone_forge_api, "_refresh_r2_stem_urls", lambda r: None)
    monkeypatch.setattr(tone_forge_api, "_render_pool", lambda: None)

    def fake_kit_borrow_job(source_id, source_result, target_bpm, source_tag="donor",
                            target_key=None, source_name=None, pad_base=0, pads=12):
        return [{
            "padIdx": i, "name": f"{source_tag} {i}", "source": source_tag,
            "sourceName": source_name or "", "stem": "other", "loopable": True,
            "loopPointSec": 0, "loopScore": 1.0, "defaultQuantize": "1 bar",
            "sampleFile": f"borrow_dead{i}.wav",
            "sourceLoopStartSec": float(i), "sourceLoopEndSec": float(i + 2),
            "transposeSemis": 0,
        } for i in range(2)]

    monkeypatch.setattr(_borrow, "kit_borrow_job", fake_kit_borrow_job)

    # Blank canvas (no session): renders the crate track's own kit as donor.
    r = _client.get("/api/crate/crate:ccmixter:0003/borrow", params={"stem": "other"})
    assert r.status_code == 200
    pack = r.json()
    assert pack["packId"] == "crate-crate:ccmixter:0003-other"
    assert pack["exportEncumbered"] is True           # BY-SA rides on the pack
    assert pack["pads"] and all(p["source"] == "donor" for p in pack["pads"])
    for p in pack["pads"]:
        assert p["attribution"]                       # CC-BY on every pad
        assert p["licenseId"] == "CC-BY-SA-4.0"
        assert p["exportEncumbered"] is True
        assert p["sampleUrl"].startswith("/api/crate/sample/")


def test_api_crate_borrow_host_plus_donor(monkeypatch):
    monkeypatch.delenv("TONEFORGE_CRATE_DIR", raising=False)
    creg.invalidate_cache()
    session = {"id": "sess", "name": "Host", "result": _blob(120, "C major")}
    monkeypatch.setattr(tone_forge_api, "_get_history_item",
                        lambda eid: session if eid == "sess" else None)
    monkeypatch.setattr(tone_forge_api, "_refresh_r2_stem_urls", lambda r: None)
    monkeypatch.setattr(tone_forge_api, "_render_pool", lambda: None)

    calls = []

    def fake_kit_borrow_job(source_id, source_result, target_bpm, source_tag="donor",
                            target_key=None, source_name=None, pad_base=0, pads=12):
        calls.append(source_tag)
        return [{
            "padIdx": 0, "name": source_tag, "source": source_tag,
            "sourceName": source_name or "", "stem": "other", "loopable": True,
            "loopPointSec": 0, "loopScore": 1.0, "defaultQuantize": "1 bar",
            "sampleFile": f"borrow_{'aa' if source_tag == 'donor' else 'bb'}0.wav",
            "sourceLoopStartSec": 0.0, "sourceLoopEndSec": 2.0, "transposeSemis": 0,
        }]

    monkeypatch.setattr(_borrow, "kit_borrow_job", fake_kit_borrow_job)
    r = _client.get("/api/crate/crate:fma:0001/borrow",
                    params={"session_id": "sess", "stem": "other"})
    assert r.status_code == 200
    pack = r.json()
    assert set(calls) == {"initial", "donor"}          # host + donor both render
    sources = [p["source"] for p in pack["pads"]]
    assert sources == ["initial", "donor"]
    # Only the donor (crate) pads carry attribution; the host's own don't.
    donor = [p for p in pack["pads"] if p["source"] == "donor"][0]
    assert donor["attribution"]
    host = [p for p in pack["pads"] if p["source"] == "initial"][0]
    assert "attribution" not in host


def test_api_crate_borrow_unknown_track_404(monkeypatch):
    monkeypatch.delenv("TONEFORGE_CRATE_DIR", raising=False)
    creg.invalidate_cache()
    r = _client.get("/api/crate/crate:nope:9/borrow")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Extraction-quality contract — HARD RULE (never compromise on extraction)
#
# The crate is ingested ONCE and matched/borrowed from forever, so the ingest
# must extract every matchable musical signal to its fullest potential — there
# must never be a reason to re-analyse a crate track because we under-extracted
# it. These tests PIN PipelineConfig.crate so a future "speed optimisation"
# that silently drops melody, stems, or downgrades the MIDI path to the slow
# ensemble (or the wrong direction) fails CI instead of shipping a degraded
# crate. See unified_pipeline.PipelineConfig.crate + tone_forge/crate/README.md.
# ---------------------------------------------------------------------------

class TestCrateExtractionQuality:
    def _cfg(self):
        from tone_forge.unified_pipeline import PipelineConfig
        return PipelineConfig.crate()

    def test_stems_are_extracted(self):
        # Best-VERSION curated kit + borrow loops are built from stems; the
        # admit gate requires the kit builder to yield a surviving pad, so
        # stems are non-negotiable.
        cfg = self._cfg()
        assert cfg.separate_stems is True
        assert cfg.force_stem_separation is True

    def test_melody_is_extracted(self):
        # extract_midi feeds the crate/match.py melody term. Dropping it (the
        # v1 mistake) silently amputates melody matching and forces a re-ingest.
        assert self._cfg().extract_midi is True

    def test_melody_at_max_fidelity_not_downgraded_for_speed(self):
        # Melody is captured at MAX fidelity (the full ensemble), NOT downgraded
        # to basic-pitch-only for speed. The ensemble being CPU-bound on a pod
        # is a LOGISTICS problem (watchdog/pods/GPU-accel), never a reason to
        # cut fidelity — the whole point of the hard rule.
        assert self._cfg().use_ensemble is True

    def test_captures_every_metric(self):
        # "Capture all data points once per run": separation-quality metrics,
        # synth timbre, provenance, and waveform are all stored so they can
        # assist matching / ranking later WITHOUT a re-ingest.
        cfg = self._cfg()
        assert cfg.analyze_quality is True
        assert cfg.detect_synth_behavior is True
        assert cfg.include_provenance is True
        assert cfg.include_waveform is True

    def test_no_extraction_signal_dropped_vs_deep(self):
        # The strongest pin: the crate must capture EVERYTHING deep() does for
        # every analysis-signal flag. A future "crate optimisation" that turns
        # any of these off (the v1/v2 mistakes) fails here.
        from tone_forge.unified_pipeline import PipelineConfig
        crate, deep = PipelineConfig.crate(), PipelineConfig.deep()
        for flag in (
            "separate_stems", "force_stem_separation", "extract_midi",
            "use_ensemble", "analyze_quality", "detect_synth_behavior",
            "include_provenance", "include_waveform",
        ):
            assert getattr(crate, flag) == getattr(deep, flag) is True, (
                f"crate dropped {flag} that deep() captures — extraction "
                f"quality compromised")

    def test_deep_mode_full_analysis(self):
        from tone_forge.unified_pipeline import AnalysisMode
        assert self._cfg().mode == AnalysisMode.DEEP

    def test_admit_gate_runs_the_same_best_version_kit_builder(self):
        # The pad-quality / best-version pipeline (flatness/collapse/parent
        # veto + parent-vs-children duel) lives in serve.kit_payload; the crate
        # admit gate must run THAT exact builder so every stored track is
        # proven to yield best-stems. Pin the call so it can't be swapped for a
        # weaker check.
        import inspect

        from tone_forge.crate import ingest as _ingest
        src = inspect.getsource(_ingest)
        assert "kit_payload" in src, "admit gate must build the curated kit"
        assert "_KIT_PADS_PER_SOURCE" in src, "must use the native per-source pad count"
