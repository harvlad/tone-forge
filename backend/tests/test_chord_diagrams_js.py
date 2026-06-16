"""Smoke tests for ``backend/static/chord_diagrams.js`` driven via the
``node`` CLI.

The JS module mixes pure logic (normalizeSymbol, lookupShape,
generateAlgorithmicShape, midiToFret) with DOM-dependent renderers
(renderChordDiagramSVG, renderLeadTabSVG). The pure functions are
tested here by invoking node against an inline driver script that
imports the module and prints JSON. The DOM renderers are exercised at
runtime in the browser (and covered by manual eyeball smoke).

If ``node`` isn't on PATH, the tests skip cleanly — the CI image and
the developer machines I've seen all have node available, but we don't
want to false-fail on a thin pytest-only environment.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = _REPO_ROOT / "static" / "chord_diagrams.js"
_REGISTRY_PATH = _REPO_ROOT / "static" / "chord_shapes.json"


pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not on PATH — JS smoke tests require node",
)


def _run_driver(driver_src: str) -> dict:
    """Run an inline JS driver that imports chord_diagrams.js and prints
    a JSON result on a single ``__RESULT__`` line. Returns the parsed
    dict."""
    # The driver script is written to a tempfile in the module's
    # directory so its relative import works without juggling module
    # resolution paths.
    tmp = _MODULE_PATH.parent / "_test_driver.mjs"
    try:
        tmp.write_text(driver_src)
        proc = subprocess.run(
            ["node", str(tmp)],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(_REPO_ROOT),
        )
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

    if proc.returncode != 0:
        raise RuntimeError(
            f"node driver failed:\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):].strip())
    raise RuntimeError(
        f"no __RESULT__ line in driver output:\n{proc.stdout!r}"
    )


def test_lookup_shape_curated_open_C_major() -> None:
    """A curated open shape ("C") must come back from the registry,
    not from the algorithmic fallback."""
    registry = json.loads(_REGISTRY_PATH.read_text())
    driver = f"""
import {{ lookupShape }} from "./chord_diagrams.js";
const reg = {json.dumps(registry)};
const shape = lookupShape("C", reg);
process.stdout.write("__RESULT__ " + JSON.stringify(shape) + "\\n");
"""
    result = _run_driver(driver)
    assert result is not None
    assert result["frets"] == [-1, 3, 2, 0, 1, 0]


def test_lookup_shape_algorithmic_fallback_for_uncovered_quality() -> None:
    """A symbol not in the registry must come back from the algorithmic
    generator with a valid 6-fret voicing."""
    registry = json.loads(_REGISTRY_PATH.read_text())
    # Remove G#:maj from registry so the test forces the algorithmic
    # path (otherwise the curated open shape would short-circuit).
    registry = {**registry, "shapes": dict(registry["shapes"])}
    registry["shapes"].pop("G#:maj", None)

    driver = f"""
import {{ lookupShape }} from "./chord_diagrams.js";
const reg = {json.dumps(registry)};
const shape = lookupShape("G#", reg);
process.stdout.write("__RESULT__ " + JSON.stringify(shape) + "\\n");
"""
    result = _run_driver(driver)
    assert result is not None, "algorithmic fallback returned null for G#"
    assert isinstance(result["frets"], list)
    assert len(result["frets"]) == 6
    for f in result["frets"]:
        assert f == -1 or 0 <= f <= 15


def test_normalize_symbol_parses_root_and_quality() -> None:
    """Pure parser: covers maj/min/7/m7/maj7/5/sus2/sus4/dim/aug."""
    driver = """
import { normalizeSymbol } from "./chord_diagrams.js";
const cases = ["C", "Am", "G7", "F#m", "Cmaj7", "Bm7", "D5", "Asus4", "Bdim", "Eaug"];
const out = cases.map((s) => [s, normalizeSymbol(s)]);
process.stdout.write("__RESULT__ " + JSON.stringify(out) + "\\n");
"""
    result = _run_driver(driver)
    table = {sym: parsed for sym, parsed in result}
    assert table["C"] == {"root": "C", "rootPc": 0, "quality": "maj"}
    assert table["Am"] == {"root": "A", "rootPc": 9, "quality": "min"}
    assert table["G7"] == {"root": "G", "rootPc": 7, "quality": "7"}
    assert table["F#m"] == {"root": "F#", "rootPc": 6, "quality": "min"}
    assert table["Cmaj7"] == {"root": "C", "rootPc": 0, "quality": "maj7"}
    assert table["Bm7"] == {"root": "B", "rootPc": 11, "quality": "m7"}
    assert table["D5"] == {"root": "D", "rootPc": 2, "quality": "5"}
    assert table["Asus4"] == {"root": "A", "rootPc": 9, "quality": "sus4"}
    assert table["Bdim"] == {"root": "B", "rootPc": 11, "quality": "dim"}
    assert table["Eaug"] == {"root": "E", "rootPc": 4, "quality": "aug"}


def test_midi_to_fret_finds_lowest_fret_assignment() -> None:
    """midiToFret picks the smallest fret across all candidate strings."""
    driver = """
import { midiToFret } from "./chord_diagrams.js";
const pitches = [40, 45, 52, 60, 69, 76];
const out = pitches.map((p) => [p, midiToFret(p)]);
process.stdout.write("__RESULT__ " + JSON.stringify(out) + "\\n");
"""
    result = _run_driver(driver)
    table = {p: assign for p, assign in result}

    # MIDI 40 (E2) is the open low E string.
    assert table[40] == {"string": 0, "fret": 0}
    # MIDI 45 (A2) is the open A string.
    assert table[45] == {"string": 1, "fret": 0}
    # MIDI 52 (E3) is fret 2 on the D string (open=50) — lowest fret.
    assert table[52] == {"string": 2, "fret": 2}
    # MIDI 60 (C4) is fret 1 on the B string (open=59) — lowest fret.
    assert table[60] == {"string": 4, "fret": 1}
    # MIDI 69 (A4) is fret 5 on the high E string (open=64).
    assert table[69] == {"string": 5, "fret": 5}
    # MIDI 76 (E5) is fret 12 on the high E.
    assert table[76] == {"string": 5, "fret": 12}


def test_list_voicings_returns_curated_plus_barres() -> None:
    """listVoicings should return at minimum the curated open shape
    and one barre alternate (E-shape) for a common major chord. Used
    by the voicing picker (§4 of the chord-guidance UX directive)."""
    registry = json.loads(_REGISTRY_PATH.read_text())
    driver = f"""
import {{ listVoicings }} from "./chord_diagrams.js";
const reg = {json.dumps(registry)};
const out = listVoicings("C", reg).map((v) => ({{
  name: v.name,
  frets: v.shape.frets,
}}));
process.stdout.write("__RESULT__ " + JSON.stringify(out) + "\\n");
"""
    result = _run_driver(driver)
    assert isinstance(result, list)
    # Curated C major is first.
    assert result[0]["name"] == "Open / canonical"
    assert result[0]["frets"] == [-1, 3, 2, 0, 1, 0]
    # At least one barre alternate must follow.
    barre_names = {entry["name"] for entry in result[1:]}
    assert barre_names & {"E-shape barre", "A-shape barre"}, (
        f"expected at least one barre alternate, got {result!r}"
    )
    # No two voicings should share the exact same fret pattern (dedup).
    fret_sigs = [tuple(entry["frets"]) for entry in result]
    assert len(fret_sigs) == len(set(fret_sigs))


def test_list_voicings_empty_for_unknown_symbol() -> None:
    """Garbage / unparseable input → empty array, not a throw."""
    registry = json.loads(_REGISTRY_PATH.read_text())
    driver = f"""
import {{ listVoicings }} from "./chord_diagrams.js";
const reg = {json.dumps(registry)};
const out = listVoicings("???", reg);
process.stdout.write("__RESULT__ " + JSON.stringify(out) + "\\n");
"""
    result = _run_driver(driver)
    assert result == []


def test_lookup_shape_returns_null_for_garbage() -> None:
    """Unrecognised input shouldn't throw — the renderer falls back to
    a symbol-only placeholder."""
    driver = """
import { lookupShape } from "./chord_diagrams.js";
const out = [lookupShape("???", {}), lookupShape("", {}), lookupShape("Xyz", {})];
process.stdout.write("__RESULT__ " + JSON.stringify(out) + "\\n");
"""
    result = _run_driver(driver)
    assert result == [None, None, None]
