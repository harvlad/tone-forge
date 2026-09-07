"""Objective per-pad quality audit for Auto Kits — measure, don't vibe.

Kit pads regularly ship slices that *sound* broken in three specific,
measurable ways, and until now the only detector was someone's ear:

1. **Inaudible pads** — the clients peak-normalize every chop to -4 dBFS
   with the boost capped at +12 dB (``min(0.63 / peak, 4.0)`` in
   jam-desktop ``ChopPlayer.normalizePeak`` / mobile SampleScheduler).
   A slice whose RMS is still below ~-30 dBFS *after* that capped boost
   reads as "pad doesn't work". Conversely, a slice whose *uncapped*
   normalize would have exceeded +12 dB is bleed-only material — the cap
   exists precisely because normalizing it turned bleed into foreground
   fuzz.
2. **Loop seam clicks** — every auto-kit pad loops its whole window, so
   an amplitude step at the wrap point (end sample vs start sample) or a
   spectral/energy mismatch between the last and first 50 ms is an
   audible click/thump on every cycle.
3. **Bar misalignment** — pads are fixed 8-second windows
   (kit_builder._SAMPLE_LEN_SEC), which is only a whole number of bars
   at tempos that divide 8 s cleanly. Off-bar loops drift against the
   shared pad cycle.

This script fetches a song's kit manifest over HTTP, renders each pad's
slice from the stem audio (the same cut ``ableton_kit_export._render_slice``
makes — auto-kit pads have no per-pad audio endpoint; only drum-kit
one-shots expose ``sampleUrl`` → /api/song/{id}/drum-sample/{fname}),
computes the three metric families with soundfile + numpy only, and
prints an aligned table plus optional JSON.

Usage:
    python scripts/audit_kit_quality.py --song "my song"          # search /api/history
    python scripts/audit_kit_quality.py --song 1a2b3c4d          # exact entry id
    python scripts/audit_kit_quality.py --song X --kind drums --json out.json
    python scripts/audit_kit_quality.py --self-test              # no server needed
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Client-parity constants — keep in lockstep with
# jam-desktop/Sources/JamDesktopAudio/ChopPlayer.swift normalizePeak()
# (and mobile's SampleScheduler, which it mirrors).
# ---------------------------------------------------------------------------
NORM_TARGET_LIN = 0.63          # -4 dBFS peak target
NORM_BOOST_CAP = 4.0            # +12 dB boost cap (bleed guard)
SILENT_PEAK_FLOOR = 1e-4        # below this the clients skip normalize entirely

INAUDIBLE_POST_RMS_DBFS = -30.0  # post-boost RMS below this = "pad doesn't work"
BLEED_UNCAPPED_DB = 12.0         # wanted more boost than the cap = bleed-only

SEAM_EDGE_SEC = 0.050            # compare last vs first 50 ms
SEAM_WIN_SEC = 0.005             # in 5 ms sub-windows
SEAM_BAD = 0.5                   # seam_score above this predicts a click/thump

BAR_TOLERANCE_MS = 5.0
_EPS = 1e-12


def _dbfs(x: float) -> float:
    return 20.0 * math.log10(max(float(x), _EPS))


# ---------------------------------------------------------------------------
# Per-slice metrics (pure numpy — shared by the HTTP path and --self-test)
# ---------------------------------------------------------------------------

def audit_slice(
    data: np.ndarray,
    sr: int,
    loopable: bool,
    tempo_bpm: Optional[float],
) -> Dict:
    """All metrics for one rendered slice. ``data`` is float samples,
    mono ``(n,)`` or ``(n, ch)``."""
    x = np.asarray(data, dtype=np.float64)
    mono = x if x.ndim == 1 else x.mean(axis=1)
    n = mono.shape[0]
    out: Dict = {"frames": int(n), "sample_rate": int(sr),
                 "duration_s": round(n / sr, 4) if sr else 0.0}
    if n == 0 or sr <= 0:
        out.update({"error": "empty slice"})
        return out

    # --- 1. levels + the normalize the clients would apply -----------------
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(np.square(x))))
    out["peak_dbfs"] = round(_dbfs(peak), 2)
    out["rms_dbfs"] = round(_dbfs(rms), 2)
    if peak > SILENT_PEAK_FLOOR:
        uncapped = NORM_TARGET_LIN / peak
        gain = min(uncapped, NORM_BOOST_CAP)
        uncapped_db = 20.0 * math.log10(uncapped)
    else:
        # Client leaves effectively-silent buffers untouched; the pad is
        # silent, and "how much boost it wanted" is unbounded — report the
        # cap-relative truth (way past bleed threshold) without inf.
        uncapped = float("inf")
        gain = 1.0
        uncapped_db = 200.0
    boost_db = 20.0 * math.log10(gain)
    post_rms_db = _dbfs(rms) + boost_db
    out["norm_boost_db"] = round(boost_db, 2)
    out["norm_uncapped_db"] = round(min(uncapped_db, 200.0), 2)
    out["post_norm_rms_dbfs"] = round(post_rms_db, 2)
    out["flag_inaudible"] = bool(post_rms_db < INAUDIBLE_POST_RMS_DBFS)
    out["flag_bleed_risk"] = bool(uncapped_db > BLEED_UNCAPPED_DB)

    # --- 2. loop seam ------------------------------------------------------
    if loopable and n >= int(2 * SEAM_EDGE_SEC * sr):
        out["seam_score"] = round(_seam_score(mono, sr, rms), 3)
        out["flag_seam"] = bool(out["seam_score"] > SEAM_BAD)
    else:
        out["seam_score"] = None
        out["flag_seam"] = False

    # --- 3. bar alignment --------------------------------------------------
    if loopable and tempo_bpm and tempo_bpm > 0:
        bar_s = 4.0 * 60.0 / float(tempo_bpm)   # assumes 4/4 — the grid does too
        dur = n / sr
        bars = max(1, round(dur / bar_s))
        off_ms = abs(dur - bars * bar_s) * 1000.0
        out["bar_multiple"] = int(bars)
        out["bar_offset_ms"] = round(off_ms, 2)
        out["flag_off_bar"] = bool(off_ms > BAR_TOLERANCE_MS)
    else:
        out["bar_multiple"] = None
        out["bar_offset_ms"] = None
        out["flag_off_bar"] = False
    return out


def _seam_score(mono: np.ndarray, sr: int, rms: float) -> float:
    """0..1 predictor of an audible click/thump at the loop wrap.

    Three ingredients, weighted so a hard DC step at the seam dominates:
      * amplitude step |x[end] - x[start]| relative to slice RMS — the
        literal discontinuity the ear hears as a click;
      * energy mismatch between the last and first 50 ms (5 ms RMS
        envelope) — a thump even when the endpoint samples happen to line up;
      * spectral mismatch (cosine distance of mean 5 ms-window magnitude
        spectra) — timbre jumping across the seam.
    """
    edge = int(SEAM_EDGE_SEC * sr)
    win = max(4, int(SEAM_WIN_SEC * sr))
    head, tail = mono[:edge], mono[-edge:]

    step = abs(float(mono[-1]) - float(mono[0]))
    step_norm = min(1.0, step / (2.0 * rms + _EPS))

    def _env(seg: np.ndarray) -> np.ndarray:
        k = len(seg) // win
        return np.sqrt(np.mean(np.square(seg[: k * win].reshape(k, win)), axis=1))

    e_head, e_tail = _env(head), _env(tail)
    eh, et = float(np.mean(e_head)), float(np.mean(e_tail))
    energy_mismatch = abs(eh - et) / (eh + et + _EPS)

    def _spec(seg: np.ndarray) -> np.ndarray:
        k = len(seg) // win
        return np.abs(np.fft.rfft(seg[: k * win].reshape(k, win), axis=1)).mean(axis=0)

    s_head, s_tail = _spec(head), _spec(tail)
    denom = float(np.linalg.norm(s_head) * np.linalg.norm(s_tail))
    spec_dist = 1.0 - (float(np.dot(s_head, s_tail)) / denom if denom > _EPS else 1.0)
    spec_dist = min(1.0, max(0.0, spec_dist))

    return min(1.0, 0.7 * step_norm + 0.15 * energy_mismatch + 0.15 * spec_dist)


# ---------------------------------------------------------------------------
# HTTP + stem plumbing
# ---------------------------------------------------------------------------

def _get_json(url: str) -> Dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path) -> Path:
    with urllib.request.urlopen(url, timeout=300) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dest


def _resolve_song(base: str, query: str) -> Tuple[str, Dict]:
    """entry id + full history entry for --song (id or search query)."""
    # Exact-id first: /api/history/{id} 404s cleanly if it isn't one.
    try:
        entry = _get_json(f"{base}/api/history/{urllib.parse.quote(query)}")
        if isinstance(entry, dict) and entry.get("id"):
            return entry["id"], entry
    except Exception:
        pass
    rows = _get_json(
        f"{base}/api/history?q={urllib.parse.quote(query)}&limit=5"
    ).get("history") or []
    if not rows:
        raise SystemExit(f"No history entry matches {query!r}")
    if len(rows) > 1:
        names = ", ".join(f"{r.get('name')!r} ({r.get('id', '')[:8]})" for r in rows)
        print(f"[audit] {len(rows)} matches — using first of: {names}", file=sys.stderr)
    entry_id = rows[0]["id"]
    return entry_id, _get_json(f"{base}/api/history/{entry_id}")


class StemStore:
    """Lazy local file per stem role. Prefers server-local paths that exist
    on THIS machine (dev box == server box); otherwise downloads the URL
    the (R2-refreshed) history entry hands out — same order stem_fetch
    uses. One fetch per role, reused across pads."""

    def __init__(self, base: str, result: Dict, scratch: Path):
        self.base = base
        self.scratch = scratch
        self._cache: Dict[str, Optional[Path]] = {}
        locals_ = result.get("stems_local")
        self.local = locals_ if isinstance(locals_, dict) else {}
        paths = result.get("stems_paths")
        self.paths = paths if isinstance(paths, dict) else {}

    def path_for(self, role: str) -> Optional[Path]:
        if role in self._cache:
            return self._cache[role]
        p = self._fetch(role)
        self._cache[role] = p
        return p

    def _fetch(self, role: str) -> Optional[Path]:
        for src in (self.local.get(role), self.paths.get(role)):
            if not isinstance(src, str) or not src:
                continue
            if src.startswith(("http://", "https://", "/api/")):
                url = src if src.startswith("http") else f"{self.base}{src}"
                dest = self.scratch / f"stem_{role}.audio"
                try:
                    return _download(url, dest)
                except Exception as exc:
                    print(f"[audit] stem {role!r} download failed: {exc}",
                          file=sys.stderr)
                    continue
            if Path(src).exists():
                return Path(src)
        return None


def _read_slice(stem_path: Path, start_sec: float, end_sec: float):
    """Same cut ableton_kit_export._render_slice makes (seek + bounded
    read at native rate), minus the PCM_16 re-encode we don't need."""
    import soundfile as sf

    with sf.SoundFile(str(stem_path)) as f:
        sr = f.samplerate
        start = max(0, int(start_sec * sr))
        stop = min(len(f), int(end_sec * sr))
        if stop <= start:
            return None, sr
        f.seek(start)
        return f.read(stop - start, dtype="float64"), sr


def _read_whole(path: Path):
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float64")
    return data, sr


def audit_kit(base: str, song: str, kind: str, pads: int) -> Dict:
    entry_id, entry = _resolve_song(base, song)
    result = entry.get("result") or {}
    tempo = result.get("tempo_bpm") or entry.get("tempo_bpm")
    tempo = float(tempo) if isinstance(tempo, (int, float)) and tempo else None

    kit = _get_json(
        f"{base}/api/song/{entry_id}/kit?pads={pads}&kind={urllib.parse.quote(kind)}"
    )
    kit_pads: List[Dict] = kit.get("pads") or []
    print(f"[audit] song={entry.get('name')!r} id={entry_id[:12]} "
          f"kind={kind} pads={len(kit_pads)} tempo={tempo}", file=sys.stderr)

    records: List[Dict] = []
    with tempfile.TemporaryDirectory(prefix="kit_audit_") as td:
        scratch = Path(td)
        stems = StemStore(base, result, scratch)
        for pad in kit_pads:
            rec = {
                "padIdx": pad.get("padIdx"),
                "name": pad.get("name"),
                "category": pad.get("category"),
                "loopable": bool(pad.get("loopable")),
            }
            sl = pad.get("stemSlice") or {}
            rec["stemRole"] = sl.get("stemRole")
            sample_url = pad.get("sampleUrl")
            try:
                if sample_url:
                    # Drum-kit one-shots: the rendered composite IS the pad
                    # audio the client plays — audit that, not the raw window.
                    dest = scratch / f"pad{rec['padIdx']:02d}.wav"
                    _download(f"{base}{sample_url}", dest)
                    data, sr = _read_whole(dest)
                else:
                    stem_path = stems.path_for(sl.get("stemRole") or "")
                    if stem_path is None:
                        rec["error"] = f"no audio for stem {sl.get('stemRole')!r}"
                        records.append(rec)
                        continue
                    data, sr = _read_slice(
                        stem_path, float(sl.get("startSec", 0.0)),
                        float(sl.get("endSec", 0.0)))
                    if data is None:
                        rec["error"] = "empty slice window"
                        records.append(rec)
                        continue
                rec.update(audit_slice(data, sr, rec["loopable"], tempo))
            except Exception as exc:  # noqa: BLE001 — one bad pad shouldn't kill the audit
                rec["error"] = str(exc)
            records.append(rec)

    return {
        "base": base, "entryId": entry_id, "song": entry.get("name"),
        "kind": kind, "tempo_bpm": tempo,
        "provenance": kit.get("provenance"),
        "pads": records,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fmt(v, width, prec=None):
    if v is None:
        return "-".rjust(width)
    if prec is not None and isinstance(v, (int, float)):
        return f"{v:.{prec}f}".rjust(width)
    return str(v).rjust(width)


def print_table(report: Dict) -> int:
    rows = report["pads"]
    print(f"\nKit audit — {report.get('song')!r}  kind={report.get('kind')}  "
          f"tempo={report.get('tempo_bpm')}")
    hdr = (f"{'pad':>3} {'name':<28} {'stem':<8} {'dur_s':>6} {'rms':>7} "
           f"{'peak':>7} {'boost':>6} {'seam':>5} {'bar':>10}  flags")
    print(hdr)
    print("-" * len(hdr))
    n_bad = 0
    for r in rows:
        flags = []
        if r.get("error"):
            flags.append(f"ERROR:{r['error']}")
        if r.get("flag_inaudible"):
            flags.append("INAUDIBLE")
        if r.get("flag_bleed_risk"):
            flags.append("BLEED-RISK")
        if r.get("flag_seam"):
            flags.append("SEAM-CLICK")
        if r.get("flag_off_bar"):
            flags.append("OFF-BAR")
        if flags:
            n_bad += 1
        bar = "-"
        if r.get("bar_multiple") is not None:
            bar = f"{r['bar_multiple']}b{r['bar_offset_ms']:+.1f}ms"
        name = (r.get("name") or "")[:28]
        print(f"{_fmt(r.get('padIdx'), 3)} {name:<28} "
              f"{(r.get('stemRole') or '-'):<8} "
              f"{_fmt(r.get('duration_s'), 6, 2)} "
              f"{_fmt(r.get('rms_dbfs'), 7, 1)} "
              f"{_fmt(r.get('peak_dbfs'), 7, 1)} "
              f"{_fmt(r.get('norm_boost_db'), 6, 1)} "
              f"{_fmt(r.get('seam_score'), 5, 2)} "
              f"{bar:>10}  {' '.join(flags) or 'ok'}")
    print(f"\n{len(rows)} pads, {n_bad} flagged")
    return n_bad


# ---------------------------------------------------------------------------
# Self-test — synthetic slices with known-good / known-bad properties
# ---------------------------------------------------------------------------

def self_test() -> int:
    sr = 44100
    tempo = 120.0                       # bar = 2.0 s
    dur = 2.0
    n = int(sr * dur)
    t = np.arange(n) / sr

    # (a) perfect loop: 220 Hz sine = exactly 440 periods in 2.0 s — the
    #     wrap point is phase-continuous, level healthy, exactly 1 bar.
    perfect = 0.5 * np.sin(2 * np.pi * 220.0 * t)

    # (b) clicky loop: non-integer period count (phase jump at the wrap)
    #     plus a DC step under the final quarter — a guaranteed seam
    #     click/thump with the same overall level.
    clicky = 0.4 * np.sin(2 * np.pi * 220.6 * t + 0.5)
    clicky[3 * n // 4:] += 0.4

    # (c) near-silent noise: capped +12 dB boost still leaves it buried,
    #     and the uncapped normalize would have been tens of dB (bleed).
    rng = np.random.default_rng(7)
    silent = 0.001 * rng.standard_normal(n)

    # (d) off-bar loop: healthy sine but 2.13 s at 120 bpm ≠ whole bars.
    n2 = int(sr * 2.13)
    off = 0.5 * np.sin(2 * np.pi * 220.0 * np.arange(n2) / sr)

    a = audit_slice(perfect, sr, loopable=True, tempo_bpm=tempo)
    b = audit_slice(clicky, sr, loopable=True, tempo_bpm=tempo)
    c = audit_slice(silent, sr, loopable=True, tempo_bpm=tempo)
    d = audit_slice(off, sr, loopable=True, tempo_bpm=tempo)

    checks = [
        ("perfect loop seam is clean", a["seam_score"] < 0.3),
        ("clicky loop seam flagged", b["seam_score"] > SEAM_BAD and b["flag_seam"]),
        ("seam ranks clicky above perfect", b["seam_score"] > a["seam_score"]),
        ("perfect loop audible", not a["flag_inaudible"]),
        ("perfect loop not bleed-risk", not a["flag_bleed_risk"]),
        ("near-silent flagged inaudible", c["flag_inaudible"]),
        ("near-silent flagged bleed-risk", c["flag_bleed_risk"]),
        ("perfect loop is 1 bar within tolerance",
         a["bar_multiple"] == 1 and not a["flag_off_bar"]),
        ("2.13 s at 120 bpm flagged off-bar", d["flag_off_bar"]),
        # The +12 dB cap is nominal; the exact linear cap is 4.0x = 12.04 dB.
        ("boost cap respected",
         c["norm_boost_db"] <= 20.0 * math.log10(NORM_BOOST_CAP) + 1e-6),
    ]
    ok = True
    for label, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and passed
    for label, m in (("perfect", a), ("clicky", b), ("silent", c), ("off-bar", d)):
        print(f"    {label:<8} rms={m['rms_dbfs']:7.1f} peak={m['peak_dbfs']:7.1f} "
              f"boost={m['norm_boost_db']:5.1f} seam={m['seam_score']} "
              f"bar_off={m['bar_offset_ms']}ms")
    print("self-test:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8300",
                    help="Backend base URL (default %(default)s)")
    ap.add_argument("--song", help="History search query or exact entry id")
    ap.add_argument("--kind", default="auto", choices=("auto", "drums"),
                    help="Kit flavor to audit (default %(default)s)")
    ap.add_argument("--pads", type=int, default=16, help="Pad count (default 16)")
    ap.add_argument("--json", metavar="PATH", help="Also write full records as JSON")
    ap.add_argument("--self-test", action="store_true",
                    help="Run metric sanity checks on synthetic slices and exit")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.song:
        ap.error("--song is required (or use --self-test)")

    report = audit_kit(args.base.rstrip("/"), args.song, args.kind, args.pads)
    n_bad = print_table(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
        print(f"wrote {args.json}")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
