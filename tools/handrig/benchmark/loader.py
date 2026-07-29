"""Benchmark loader (M0). Reads the versioned benchmark/ data into typed
objects and validates required fields. No planner, no solver, no metrics.

Run: python3 loader.py [benchmark_dir]   # prints parsed suite/phrase/ref
"""
from __future__ import annotations
from dataclasses import dataclass, field
import json, os, sys

REQUIRED_EVENT = {"t", "dur", "string", "fret", "finger", "articulation"}
REQUIRED_PHRASE = {"id", "category", "style", "instrument", "events"}
REQUIRED_REF = {"id", "reference_A_mechanical", "reference_B_human"}


@dataclass
class Event:
    t: float; dur: float; string: int; fret: int
    finger: str; articulation: str; target: object = None


@dataclass
class Phrase:
    id: str; category: str; style: str
    instrument: dict; tempo_bpm: float; events: list
    reason: str = ""


@dataclass
class Reference:
    id: str; A: dict; B: dict; style: str = "neutral"; labeller: str = ""


@dataclass
class Suite:
    version: str; phrases: list; weights: dict
    instrument_default: dict; metric_version: str


def _need(d: dict, keys: set, what: str):
    missing = keys - set(d)
    if missing:
        raise ValueError(f"{what}: missing required fields {sorted(missing)}")


def load_suite(bdir: str) -> Suite:
    d = json.load(open(os.path.join(bdir, "suite.json")))
    return Suite(d["benchmark_version"], d["phrases"], d.get("metric_weights", {}),
                 d.get("instrument_default", {}), d.get("metric_version", "0"))


def load_phrase(bdir: str, pid: str) -> Phrase:
    d = json.load(open(os.path.join(bdir, "phrases", f"{pid}.json")))
    _need(d, REQUIRED_PHRASE, f"phrase {pid}")
    events = []
    for i, e in enumerate(d["events"]):
        _need(e, REQUIRED_EVENT, f"phrase {pid} event {i}")
        events.append(Event(e["t"], e["dur"], e["string"], e["fret"],
                            e["finger"], e["articulation"], e.get("target")))
    return Phrase(d["id"], d["category"], d["style"], d["instrument"],
                  d.get("tempo_bpm", 0), events, d.get("reason", ""))


def load_reference(bdir: str, pid: str) -> Reference:
    d = json.load(open(os.path.join(bdir, "references", f"{pid}.json")))
    _need(d, REQUIRED_REF, f"reference {pid}")
    return Reference(d["id"], d["reference_A_mechanical"], d["reference_B_human"],
                     d.get("style", "neutral"), d.get("labeller", ""))


if __name__ == "__main__":
    bdir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    suite = load_suite(bdir)
    print(f"SUITE v{suite.version}  phrases={suite.phrases}  metric_v={suite.metric_version}")
    for pid in suite.phrases:
        ph = load_phrase(bdir, pid)
        ref = load_reference(bdir, pid)
        print(f"PHRASE {ph.id} [{ph.category}/{ph.style}] {len(ph.events)} event(s), "
              f"{ph.instrument['n_strings']}-string @ {ph.tempo_bpm}bpm")
        for e in ph.events:
            print(f"   t={e.t} str{e.string} fret{e.fret} {e.finger} ({e.articulation})")
        print(f"REF {ref.id}: A.shift={ref.A['expected']['shift_count']} "
              f"B.min_shifts={ref.B['constraints']['min_practical_shifts']} "
              f"B.acceptable_fingerings={len(ref.B['acceptable_fingerings'])}")
    print("LOADER_OK")
