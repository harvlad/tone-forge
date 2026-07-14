#!/usr/bin/env python3
"""Procedurally synthesise curated sample packs.

Renders N packs of 8 one-shot pads each into
``static/samples/<packId>/pads/NN_name.m4a`` (mono, 44.1k AAC),
writes each pack's ``manifest.json`` (iOS SampleBank shape), and
appends the pack rows to ``static/samples/catalog.json``.

Pure-DSP: sines, filtered noise, additive stacks, pitch sweeps.
No sample assets required. Idempotent per pack — reruns overwrite
the pack dir and dedupe the catalog by packId.

Usage:
    python3 scripts/generate_sample_packs.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import uuid
import wave
from pathlib import Path

import numpy as np

SR = 44100
SAMPLES_ROOT = Path(__file__).resolve().parents[1] / "static" / "samples"

# Stable namespace for deterministic pattern/track UUIDs. Re-running the
# generator (or the iOS client re-activating a pack) always yields the
# same ids, so SequencerPatternStore.save() dedupes by id instead of
# piling up duplicate grooves.
_UUID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "toneforge.sequencer")

# Rights metadata stamped into every generated manifest + catalog row.
# These packs are synthesized from scratch (sines, noise, additive
# stacks) by this script — no third-party recordings, no cleared
# samples needed. Recording this in the manifest gives the client and
# any future audit a machine-readable provenance trail.
PACK_LICENSE = (
    "Proprietary — © ToneForge. Licensed for playback and remixing "
    "within the ToneForge app only; not for standalone redistribution."
)
PACK_PROVENANCE = (
    "Synthesized in-house by scripts/generate_sample_packs.py "
    "(pure DSP: sines, filtered noise, additive stacks). Contains no "
    "third-party recordings."
)


# --------------------------------------------------------------------------
# DSP primitives
# --------------------------------------------------------------------------

def _t(length: float) -> np.ndarray:
    return np.linspace(0.0, length, int(SR * length), endpoint=False)


def _exp_env(length: float, rate: float, attack: float = 0.002) -> np.ndarray:
    t = _t(length)
    env = np.exp(-t * rate)
    a = int(SR * attack)
    if a > 1:
        env[:a] *= np.linspace(0.0, 1.0, a)
    return env


def _fir_lowpass(x: np.ndarray, cutoff_frac: float) -> np.ndarray:
    # cutoff_frac in (0,1) of Nyquist. Windowed-sinc, cheap + vectorised.
    n = 63
    m = (n - 1) / 2
    k = np.arange(n) - m
    h = np.sinc(cutoff_frac * k) * np.hanning(n)
    h /= h.sum()
    return np.convolve(x, h, mode="same")


def _highpass(x: np.ndarray, cutoff_frac: float) -> np.ndarray:
    return x - _fir_lowpass(x, cutoff_frac)


def _noise(length: float) -> np.ndarray:
    return np.random.uniform(-1.0, 1.0, int(SR * length))


def _saw(freq: float, length: float) -> np.ndarray:
    t = _t(length)
    return 2.0 * (t * freq - np.floor(0.5 + t * freq))


def _sine(freq: float, length: float, phase: float = 0.0) -> np.ndarray:
    return np.sin(2 * np.pi * freq * _t(length) + phase)


# --------------------------------------------------------------------------
# Voice synths — each returns a mono float array
# --------------------------------------------------------------------------

def syn_kick(f0=120.0, f1=45.0, length=0.55, rate=9.0):
    t = _t(length)
    sweep = f1 + (f0 - f1) * np.exp(-t * 22.0)
    phase = 2 * np.pi * np.cumsum(sweep) / SR
    body = np.sin(phase) * _exp_env(length, rate)
    click = _noise(length) * _exp_env(length, 120.0) * 0.3
    return body + click


def syn_808(freq=55.0, length=1.1, rate=3.2):
    t = _t(length)
    sweep = freq + 60.0 * np.exp(-t * 30.0)
    phase = 2 * np.pi * np.cumsum(sweep) / SR
    return np.sin(phase) * _exp_env(length, rate)


def syn_sub(freq=50.0, length=0.9, rate=4.0):
    return _sine(freq, length) * _exp_env(length, rate)


def syn_bass(freq=70.0, length=0.6, rate=6.0):
    x = _saw(freq, length) + 0.6 * _sine(freq / 2, length)
    x = _fir_lowpass(x, 0.10)
    return x * _exp_env(length, rate)


def syn_reese(freq=55.0, length=1.0, rate=2.5):
    x = _saw(freq, length) + _saw(freq * 1.01, length) + _saw(freq * 0.99, length)
    x = _fir_lowpass(x, 0.12)
    return x * _exp_env(length, rate)


def syn_snare(length=0.35, tone=185.0, rate=24.0):
    noise = _highpass(_noise(length), 0.25) * _exp_env(length, rate)
    body = _sine(tone, length) * _exp_env(length, 30.0) * 0.5
    return noise + body


def syn_clap(length=0.35):
    out = np.zeros(int(SR * length))
    for d in (0.0, 0.012, 0.024, 0.036):
        seg = _highpass(_noise(length), 0.3)
        env = np.exp(-np.maximum(_t(length) - d, 0) * 60.0)
        env[_t(length) < d] = 0.0
        out += seg * env
    tail = _highpass(_noise(length), 0.2) * _exp_env(length, 12.0) * 0.4
    return out + tail


def syn_hat(length=0.09, rate=90.0, hp=0.55):
    return _highpass(_noise(length), hp) * _exp_env(length, rate)


def syn_openhat(length=0.4, rate=11.0, hp=0.5):
    return _highpass(_noise(length), hp) * _exp_env(length, rate)


def syn_ride(length=0.6, rate=6.0):
    partials = sum(_sine(f, length) for f in (520, 770, 1180, 1600))
    metal = _highpass(_noise(length), 0.45)
    return (0.5 * partials + metal) * _exp_env(length, rate)


def syn_shaker(length=0.16, rate=45.0):
    return _highpass(_noise(length), 0.6) * _exp_env(length, rate, attack=0.01)


def syn_tom(freq=140.0, length=0.4, rate=10.0):
    t = _t(length)
    sweep = freq * (0.6 + 0.4 * np.exp(-t * 14.0))
    phase = 2 * np.pi * np.cumsum(sweep) / SR
    return np.sin(phase) * _exp_env(length, rate)


def syn_rim(length=0.12, freq=440.0, rate=70.0):
    body = _sine(freq, length) + 0.5 * _sine(freq * 1.6, length)
    return body * _exp_env(length, rate)


def syn_clave(length=0.1, freq=1200.0, rate=60.0):
    return _sine(freq, length) * _exp_env(length, rate)


def syn_conga(freq=220.0, length=0.35, rate=12.0):
    body = _sine(freq, length) + 0.3 * _sine(freq * 2.0, length)
    slap = _highpass(_noise(length), 0.4) * _exp_env(length, 60.0) * 0.3
    return body * _exp_env(length, rate) + slap


def syn_bell(freq=880.0, length=1.4, rate=3.5):
    ratios = (1.0, 2.01, 2.76, 3.9, 5.1)
    amps = (1.0, 0.6, 0.4, 0.25, 0.15)
    x = sum(a * _sine(freq * r, length) for r, a in zip(ratios, amps))
    return x * _exp_env(length, rate)


def syn_additive(freq=330.0, length=0.8, rate=6.0, harmonics=6, attack=0.004):
    x = sum((1.0 / (h + 1)) * _sine(freq * (h + 1), length) for h in range(harmonics))
    return x * _exp_env(length, rate, attack=attack)


def syn_pluck(freq=440.0, length=0.5, rate=9.0):
    x = _saw(freq, length)
    x = _fir_lowpass(x, 0.25)
    return x * _exp_env(length, rate)


def syn_organ(freq=220.0, length=0.9, rate=5.0):
    x = sum(_sine(freq * m, length) for m in (1, 2, 3, 4))
    return x * _exp_env(length, rate, attack=0.01)


def syn_supersaw(freq=440.0, length=0.9, rate=4.0):
    dets = (-0.02, -0.01, 0.0, 0.01, 0.02)
    x = sum(_saw(freq * (1 + d), length) for d in dets)
    x = _fir_lowpass(x, 0.35)
    return x * _exp_env(length, rate, attack=0.01)


def syn_pad(freq=220.0, length=2.4, rate=1.4):
    dets = (-0.012, -0.006, 0.006, 0.012)
    x = sum(_saw(freq * (1 + d), length) for d in dets)
    x += sum(_saw(freq * 2 * (1 + d), length) for d in dets) * 0.4
    x = _fir_lowpass(x, 0.18)
    t = _t(length)
    swell = np.minimum(t / (length * 0.4), 1.0) * np.exp(-t * rate * 0.15)
    return x * swell


def syn_drone(freq=110.0, length=2.6, rate=0.9):
    x = _saw(freq, length) + _saw(freq * 1.005, length) + _sine(freq * 2, length) * 0.3
    x = _fir_lowpass(x, 0.12)
    t = _t(length)
    swell = np.minimum(t / (length * 0.5), 1.0)
    return x * swell


def syn_wash(length=2.4):
    x = _fir_lowpass(_noise(length), 0.15)
    t = _t(length)
    lfo = 0.5 + 0.5 * np.sin(2 * np.pi * 0.5 * t)
    swell = np.minimum(t / (length * 0.6), 1.0) * (0.6 + 0.4 * lfo)
    return x * swell


def syn_crackle(length=2.2, density=0.004):
    n = int(SR * length)
    out = np.zeros(n)
    idx = np.random.rand(n) < density
    out[idx] = np.random.uniform(-1, 1, idx.sum())
    hiss = _highpass(_noise(length), 0.5) * 0.05
    return out + hiss


def syn_riser(length=2.0, f0=200.0, f1=2000.0):
    t = _t(length)
    freq = f0 * (f1 / f0) ** (t / length)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    tone = np.sin(phase)
    noise = _highpass(_noise(length), 0.3)
    x = 0.5 * tone + 0.5 * noise
    return x * np.minimum(t / length + 0.05, 1.0) ** 2


def syn_whoosh(length=1.6):
    x = _noise(length)
    t = _t(length)
    frac = 0.05 + 0.4 * (t / length)
    # coarse time-varying lowpass: blend two static filters
    lo = _fir_lowpass(x, 0.08)
    hi = _fir_lowpass(x, 0.45)
    blend = (frac - 0.05) / 0.4
    env = np.sin(np.pi * t / length)
    return (lo * (1 - blend) + hi * blend) * env


def syn_zap(length=0.35, f0=1800.0, f1=120.0):
    t = _t(length)
    freq = f1 + (f0 - f1) * np.exp(-t * 18.0)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return np.sin(phase) * _exp_env(length, 10.0)


def syn_siren(length=1.2, base=600.0, depth=300.0, rate_hz=3.0):
    t = _t(length)
    freq = base + depth * np.sin(2 * np.pi * rate_hz * t)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    env = np.minimum(t / 0.1, 1.0) * np.minimum((length - t) / 0.1, 1.0)
    return np.sin(phase) * np.clip(env, 0, 1)


def syn_boom(freq=48.0, length=1.4, rate=2.6):
    sub = _sine(freq, length) * _exp_env(length, rate)
    crack = _fir_lowpass(_noise(length), 0.2) * _exp_env(length, 20.0) * 0.4
    return sub + crack


def syn_braam(freq=65.0, length=2.0, rate=1.6):
    dets = (-0.01, 0.0, 0.01, 0.02)
    x = sum(_saw(freq * (1 + d), length) for d in dets)
    x += sum(_saw(freq * 2 * (1 + d), length) for d in dets) * 0.5
    x = _fir_lowpass(x, 0.14)
    t = _t(length)
    return x * np.minimum(t / 0.2, 1.0) * np.exp(-t * rate * 0.2)


def syn_impact(length=1.2):
    boom = syn_boom(length=length)
    noise = _fir_lowpass(_noise(length), 0.3) * _exp_env(length, 8.0)
    return boom + 0.5 * noise


def syn_pulse(freq=110.0, length=1.4, rate_hz=6.0):
    t = _t(length)
    tone = _saw(freq, length)
    tone = _fir_lowpass(tone, 0.2)
    gate = (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * rate_hz * t)))
    env = np.exp(-t * 1.2)
    return tone * gate * env


def syn_vox(freq=330.0, length=0.7, rate=5.0):
    # cheap formant-ish chop: fundamental + two formant sines + vibrato
    t = _t(length)
    vib = 1 + 0.02 * np.sin(2 * np.pi * 5.5 * t)
    x = _sine(freq, length) * vib
    x += 0.5 * _sine(freq * 2.4, length)
    x += 0.3 * _sine(freq * 3.1, length)
    return x * _exp_env(length, rate, attack=0.02)


def syn_stab(freq=294.0, length=0.5, rate=8.0):
    x = _saw(freq, length) + _saw(freq * 1.5, length) * 0.5 + _saw(freq * 2, length) * 0.3
    x = _fir_lowpass(x, 0.28)
    return x * _exp_env(length, rate, attack=0.003)


def syn_reverse_swell(length=1.4):
    x = syn_pad(length=length)
    return x[::-1].copy()


SYNTHS = {
    "kick": syn_kick, "808": syn_808, "sub": syn_sub, "bass": syn_bass,
    "reese": syn_reese, "snare": syn_snare, "clap": syn_clap, "hat": syn_hat,
    "openhat": syn_openhat, "ride": syn_ride, "shaker": syn_shaker,
    "tom": syn_tom, "rim": syn_rim, "clave": syn_clave, "conga": syn_conga,
    "bell": syn_bell, "additive": syn_additive, "pluck": syn_pluck,
    "organ": syn_organ, "supersaw": syn_supersaw, "pad": syn_pad,
    "drone": syn_drone, "wash": syn_wash, "crackle": syn_crackle,
    "riser": syn_riser, "whoosh": syn_whoosh, "zap": syn_zap, "siren": syn_siren,
    "boom": syn_boom, "braam": syn_braam, "impact": syn_impact,
    "pulse": syn_pulse, "vox": syn_vox, "stab": syn_stab,
    "reverse_swell": syn_reverse_swell,
}


# --------------------------------------------------------------------------
# Render helpers
# --------------------------------------------------------------------------

def _finalize(x: np.ndarray) -> np.ndarray:
    peak = np.max(np.abs(x)) or 1.0
    x = x / peak * 0.9
    f = int(SR * 0.005)
    if f > 1:
        x[:f] *= np.linspace(0, 1, f)
        x[-f:] *= np.linspace(1, 0, f)
    return x.astype(np.float32)


def _write_wav(path: Path, x: np.ndarray) -> None:
    pcm = np.clip(x, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())


def _to_m4a(wav: Path, m4a: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
         "-c:a", "aac", "-b:a", "128k", str(m4a)],
        check=True,
    )


def _snake(name: str) -> str:
    return name.lower().replace(" ", "_").replace("-", "_")


# --------------------------------------------------------------------------
# Pack definitions
# --------------------------------------------------------------------------

def pad(name, family, kind, **params):
    return {"name": name, "family": family, "kind": kind, "params": params}


PACKS = [
    {
        "packId": "techno-warehouse", "name": "Techno Warehouse",
        "family": "percussion", "paletteHint": "steel",
        "tags": ["techno", "peaktime", "industrial"],
        "genres": ["techno", "industrial"], "moods": ["driving", "dark", "hypnotic"],
        "description": "Punchy peak-time techno drums, sub stabs and a warehouse riser.",
        "pads": [
            pad("Kick", "percussion", "kick", f0=140, f1=48, length=0.5),
            pad("Clap", "percussion", "clap"),
            pad("Rim", "percussion", "rim"),
            pad("Closed Hat", "percussion", "hat"),
            pad("Open Hat", "percussion", "openhat"),
            pad("Sub Stab", "bass", "sub", freq=55, length=0.5),
            pad("Acid Stab", "stabs", "stab", freq=196, length=0.4),
            pad("Riser", "fx", "riser", length=2.0),
        ],
    },
    {
        "packId": "trap-808", "name": "Trap 808",
        "family": "mixed", "paletteHint": "crimson",
        "tags": ["trap", "808", "hiphop"],
        "genres": ["trap", "hip hop"], "moods": ["hard", "moody", "bouncy"],
        "description": "Booming 808s, snappy snares, hat rolls and a siren.",
        "pads": [
            pad("808 Kick", "percussion", "kick", f0=110, f1=40, length=0.6),
            pad("Snare", "percussion", "snare"),
            pad("Hat", "percussion", "hat", length=0.06),
            pad("Clap", "percussion", "clap"),
            pad("808 Bass", "bass", "808", freq=48, length=1.2),
            pad("Vox Chop", "vocals", "vox", freq=392),
            pad("Brass Stab", "stabs", "stab", freq=220, length=0.5),
            pad("Siren", "fx", "siren"),
        ],
    },
    {
        "packId": "house-classic", "name": "Classic House",
        "family": "mixed", "paletteHint": "gold",
        "tags": ["house", "classic", "piano"],
        "genres": ["house", "deep house"], "moods": ["uplifting", "groovy", "warm"],
        "description": "Four-on-the-floor kit, piano stabs, organ bass and a sweep.",
        "pads": [
            pad("Kick", "percussion", "kick", f0=120, f1=50, length=0.45),
            pad("Clap", "percussion", "clap"),
            pad("Shaker", "percussion", "shaker"),
            pad("Piano Stab", "stabs", "additive", freq=523, harmonics=5, length=0.6),
            pad("Organ Bass", "bass", "organ", freq=98, length=0.7),
            pad("Rhodes", "stabs", "additive", freq=349, harmonics=4, length=0.8),
            pad("Vox Hit", "vocals", "vox", freq=440),
            pad("Sweep", "fx", "whoosh"),
        ],
    },
    {
        "packId": "ambient-drift", "name": "Ambient Drift",
        "family": "textures", "paletteHint": "teal",
        "tags": ["ambient", "drone", "cinematic"],
        "genres": ["ambient", "drone"], "moods": ["calm", "spacious", "dreamy"],
        "description": "Slow pads, glass bells, wind noise and evolving drones.",
        "pads": [
            pad("Air Pad", "pads", "pad", freq=196, length=2.6),
            pad("Deep Drone", "textures", "drone", freq=98),
            pad("Glass Bell", "stabs", "bell", freq=1046, length=1.6),
            pad("Wind Wash", "textures", "wash"),
            pad("Sub Swell", "bass", "sub", freq=44, length=1.6, rate=1.5),
            pad("Chime", "stabs", "bell", freq=1568, length=1.4),
            pad("Grain Wash", "textures", "crackle"),
            pad("Rise", "fx", "riser", length=2.4, f0=150, f1=1500),
        ],
    },
    {
        "packId": "synthwave-neon", "name": "Synthwave Neon",
        "family": "pads", "paletteHint": "magenta",
        "tags": ["synthwave", "retro", "80s"],
        "genres": ["synthwave", "retrowave"], "moods": ["nostalgic", "neon", "driving"],
        "description": "Analog pads, gated snare, arp plucks and a laser zap.",
        "pads": [
            pad("Analog Pad", "pads", "pad", freq=262, length=2.4),
            pad("Gated Snare", "percussion", "snare", length=0.5, rate=14),
            pad("Tom", "percussion", "tom", freq=160),
            pad("Arp Pluck", "stabs", "pluck", freq=523),
            pad("Synth Bass", "bass", "bass", freq=82, length=0.6),
            pad("Lead Stab", "stabs", "supersaw", freq=440, length=0.7),
            pad("Reverse", "fx", "reverse_swell"),
            pad("Zap", "fx", "zap"),
        ],
    },
    {
        "packId": "jungle-breaks", "name": "Jungle Breaks",
        "family": "percussion", "paletteHint": "lime",
        "tags": ["jungle", "dnb", "breakbeat"],
        "genres": ["jungle", "drum and bass"], "moods": ["frantic", "rolling", "raw"],
        "description": "Chopped break hits, Reese bass, sub and an air horn.",
        "pads": [
            pad("Break Kick", "percussion", "kick", f0=130, f1=52, length=0.4),
            pad("Break Snare", "percussion", "snare", length=0.3),
            pad("Ghost Snare", "percussion", "snare", length=0.18, rate=40),
            pad("Ride", "percussion", "ride"),
            pad("Reese Bass", "bass", "reese", freq=55),
            pad("Sub", "bass", "sub", freq=41, length=1.0),
            pad("Stab", "stabs", "stab", freq=147, length=0.4),
            pad("Air Horn", "fx", "siren", base=500, depth=200),
        ],
    },
    {
        "packId": "dub-techno", "name": "Dub Techno",
        "family": "textures", "paletteHint": "slate",
        "tags": ["dub", "techno", "chord"],
        "genres": ["dub techno", "minimal"], "moods": ["deep", "foggy", "hypnotic"],
        "description": "Muffled kicks, dub chord stabs, hiss and a wide pad.",
        "pads": [
            pad("Kick", "percussion", "kick", f0=110, f1=46, length=0.5, rate=12),
            pad("Rim", "percussion", "rim"),
            pad("Chord Stab", "stabs", "additive", freq=294, harmonics=5, length=0.5),
            pad("Dub Chord", "pads", "pad", freq=196, length=2.2),
            pad("Sub Bass", "bass", "sub", freq=49, length=0.9),
            pad("Hiss", "textures", "hat", length=0.3, rate=8, hp=0.4),
            pad("Delay Tick", "percussion", "clave", freq=900),
            pad("Wide Pad", "pads", "drone", freq=131),
        ],
    },
    {
        "packId": "future-bass", "name": "Future Bass",
        "family": "mixed", "paletteHint": "violet",
        "tags": ["future", "bass", "edm"],
        "genres": ["future bass", "edm"], "moods": ["euphoric", "bright", "energetic"],
        "description": "Supersaw stabs, punchy kit, vox chops and a big riser.",
        "pads": [
            pad("Kick", "percussion", "kick", f0=135, f1=50, length=0.45),
            pad("Snare", "percussion", "snare"),
            pad("Hat", "percussion", "hat"),
            pad("Supersaw Stab", "stabs", "supersaw", freq=523, length=0.8),
            pad("Bass", "bass", "bass", freq=73, length=0.5),
            pad("Vox Chop", "vocals", "vox", freq=587),
            pad("Pluck", "stabs", "pluck", freq=659),
            pad("Riser", "fx", "riser", length=2.2, f0=250, f1=2500),
        ],
    },
    {
        "packId": "cinematic-tension", "name": "Cinematic Tension",
        "family": "fx", "paletteHint": "indigo",
        "tags": ["cinematic", "trailer", "score"],
        "genres": ["cinematic", "score"], "moods": ["tense", "epic", "dark"],
        "description": "Braams, sub drops, pulses, impacts and whooshes for scoring.",
        "pads": [
            pad("Boom", "fx", "boom"),
            pad("Sub Drop", "bass", "sub", freq=38, length=1.4, rate=1.4),
            pad("Braam", "fx", "braam"),
            pad("Pulse", "textures", "pulse"),
            pad("Riser", "fx", "riser", length=2.4, f0=120, f1=1800),
            pad("Impact", "fx", "impact"),
            pad("Whoosh", "fx", "whoosh"),
            pad("Drone", "textures", "drone", freq=87),
        ],
    },
    {
        "packId": "afrobeat-percussion", "name": "Afrobeat Percussion",
        "family": "percussion", "paletteHint": "orange",
        "tags": ["afrobeat", "percussion", "world"],
        "genres": ["afrobeat", "world"], "moods": ["lively", "warm", "organic"],
        "description": "Congas, shakers, bells, claves and a woody log drum.",
        "pads": [
            pad("Conga High", "percussion", "conga", freq=260),
            pad("Conga Low", "percussion", "conga", freq=180),
            pad("Shaker", "percussion", "shaker"),
            pad("Bell", "percussion", "clave", freq=1400),
            pad("Clave", "percussion", "clave", freq=1050),
            pad("Kick", "percussion", "kick", f0=115, f1=55, length=0.4),
            pad("Log Drum", "percussion", "tom", freq=200, length=0.35),
            pad("Snap", "percussion", "hat", length=0.05, rate=110, hp=0.4),
        ],
    },
]


# --------------------------------------------------------------------------
# Default sequence (procedural 16-step groove)
# --------------------------------------------------------------------------

_STEPS = 16


def _euclid(hits: int, steps: int = _STEPS) -> list[int]:
    """Evenly spread `hits` onsets across `steps` (Bjorklund-ish)."""
    if hits <= 0:
        return []
    return sorted({round(i * steps / hits) % steps for i in range(hits)})


def _role_pattern(name: str, family: str) -> tuple[list[int], float] | None:
    """(active step indices, velocity) for a pad, or None to skip.

    Name keywords win over family so a pack's "Open Hat" and "Ghost
    Snare" get their own feel. Non-rhythmic families (fx, pads,
    textures, vocals) return None → no track in the groove.
    """
    n = name.lower()
    if "kick" in n:
        return ([0, 4, 8, 12], 1.0)
    if "ghost" in n:
        return ([3, 7, 11, 15], 0.5)
    if "snare" in n or "clap" in n:
        return ([4, 12], 0.95)
    if "open" in n and "hat" in n:
        return ([2, 6, 10, 14], 0.75)
    if "hat" in n or "hihat" in n:
        return (list(range(0, _STEPS, 2)), 0.7)
    if "shaker" in n:
        return (list(range(_STEPS)), 0.5)
    if "ride" in n:
        return ([0, 4, 8, 12], 0.6)
    if "rim" in n or "clave" in n or "tick" in n or "snap" in n or "bell" in n:
        return ([6, 14], 0.7)
    if "conga" in n or "tom" in n or "log" in n:
        return ([2, 7, 11], 0.7)

    if family == "percussion":
        return (_euclid(4), 0.8)
    if family == "bass":
        return ([0, 8], 0.9)
    if family == "stabs":
        return ([0], 0.85)
    return None


def _steps_array(active: list[int], velocity: float) -> list[dict]:
    on = set(active)
    return [
        {"velocity": velocity if i in on else 0.0, "probability": 1.0}
        for i in range(_STEPS)
    ]


def build_default_sequence(pack: dict) -> dict | None:
    """A looping 16-step starter groove referencing this pack's pads.

    Wire shape matches iOS `SequencerPattern` Codable; track `chopRef`
    is a `.packPad(packId, padIdx)`. Deterministic ids from packId.
    Returns None if no pad is rhythmic (no track would fire).
    """
    pack_id = pack["packId"]
    tracks = []
    for idx, p in enumerate(pack["pads"]):
        pat = _role_pattern(p["name"], p["family"])
        if pat is None:
            continue
        active, velocity = pat
        if not active:
            continue
        track_id = str(uuid.uuid5(_UUID_NS, f"track:{pack_id}:{idx}"))
        tracks.append({
            "id": track_id,
            "chopRef": {"type": "packPad", "packId": pack_id, "padIdx": idx},
            "steps": _steps_array(active, velocity),
            "volume": 1.0,
            "pan": 0.0,
            "isMuted": False,
            "isSoloed": False,
            "name": p["name"],
        })

    if not tracks:
        return None

    return {
        "id": str(uuid.uuid5(_UUID_NS, f"pattern:{pack_id}")),
        "name": f"{pack['name']} Groove",
        "stepCount": _STEPS,
        "tracks": tracks,
        "swing": 0.0,
        "isLooping": True,
    }


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build_pack(pack: dict, tmp: Path) -> dict:
    pack_dir = SAMPLES_ROOT / pack["packId"]
    pads_dir = pack_dir / "pads"
    pads_dir.mkdir(parents=True, exist_ok=True)

    manifest_pads = []
    for idx, p in enumerate(pack["pads"]):
        synth = SYNTHS[p["kind"]]
        audio = _finalize(synth(**p["params"]))
        filename = f"{idx:02d}_{_snake(p['name'])}.m4a"
        wav_path = tmp / f"{pack['packId']}_{filename}.wav"
        _write_wav(wav_path, audio)
        _to_m4a(wav_path, pads_dir / filename)
        manifest_pads.append({
            "padIdx": idx,
            "name": p["name"],
            "family": p["family"],
            "filename": filename,
            "colorHint": None,
            "chokeGroup": None,
            "loopPointSec": None,
            "gainDb": 0,
            "defaultQuantize": None,
        })

    manifest = {
        "manifestVersion": 2,
        "packId": pack["packId"],
        "name": pack["name"],
        "family": pack["family"],
        "paletteHint": pack["paletteHint"],
        "license": PACK_LICENSE,
        "provenance": PACK_PROVENANCE,
        "pads": manifest_pads,
    }
    groove = build_default_sequence(pack)
    if groove is not None:
        manifest["defaultSequence"] = groove
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return {
        "packId": pack["packId"],
        "name": pack["name"],
        "family": pack["family"],
        "paletteHint": pack["paletteHint"],
        "tags": pack["tags"],
        "genres": pack["genres"],
        "moods": pack["moods"],
        "sizeBytes": None,
        "coverUrl": None,
        "previewUrl": None,
        "description": pack["description"],
        "padCount": len(pack["pads"]),
        "license": PACK_LICENSE,
        "provenance": PACK_PROVENANCE,
    }


def patch_manifests() -> None:
    """Rewrite existing manifest.json files in place — bump to v2 and
    inject the default groove — without re-rendering any audio. Keeps
    the already-generated pad list. Use after adding grooves so we
    don't pay the ffmpeg render cost again.
    """
    patched = 0
    # Scan every pack dir on disk (not just PACKS) so packs generated
    # by an older run without a current PACKS entry still get the groove.
    # The groove is derived from the manifest's own pad list, so a
    # PACKS entry is not required.
    for manifest_path in sorted(SAMPLES_ROOT.glob("*/manifest.json")):
        pack_id = manifest_path.parent.name
        manifest = json.loads(manifest_path.read_text())
        manifest["manifestVersion"] = 2
        manifest.setdefault("license", PACK_LICENSE)
        manifest.setdefault("provenance", PACK_PROVENANCE)
        groove = build_default_sequence(manifest)
        if groove is not None:
            manifest["defaultSequence"] = groove
        else:
            manifest.pop("defaultSequence", None)
        manifest_path.write_text(json.dumps(manifest, indent=2))
        tracks = len(groove["tracks"]) if groove else 0
        print(f"patched {pack_id} (groove: {tracks} tracks)")
        patched += 1
    print(f"patched {patched} manifests")

    # Stamp rights metadata onto existing catalog rows too.
    catalog_path = SAMPLES_ROOT / "catalog.json"
    if catalog_path.is_file():
        catalog = json.loads(catalog_path.read_text())
        for row in catalog.get("packs", []):
            row.setdefault("license", PACK_LICENSE)
            row.setdefault("provenance", PACK_PROVENANCE)
        catalog_path.write_text(json.dumps(catalog, indent=2))
        print(f"patched catalog ({len(catalog.get('packs', []))} rows)")


def main() -> None:
    if "--patch" in sys.argv:
        patch_manifests()
        return
    np.random.seed(1234)
    catalog_path = SAMPLES_ROOT / "catalog.json"
    catalog = {"packs": []}
    if catalog_path.is_file():
        catalog = json.loads(catalog_path.read_text())
    existing = {p["packId"]: p for p in catalog.get("packs", [])}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for pack in PACKS:
            entry = build_pack(pack, tmp)
            existing[entry["packId"]] = entry
            print(f"built {entry['packId']} ({entry['padCount']} pads)")

    catalog["packs"] = list(existing.values())
    catalog_path.write_text(json.dumps(catalog, indent=2))
    print(f"catalog now has {len(catalog['packs'])} packs")


if __name__ == "__main__":
    main()
