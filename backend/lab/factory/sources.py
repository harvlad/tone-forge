"""SourceProvider — one ingestion abstraction for every source.

Mirrors the design philosophy of SeparatorProvider / ModelAdapter: downstream
stages depend only on this Protocol, never on a concrete source layout. Adding a
new source (licensed set, commissioned DI, user-contributed) is one new class and
zero downstream change.

`dataset_key` ties each provider to lab.training_data.TRAINING_DATA_REGISTRY, the
single source of truth for license facts — the provider never restates a license.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from .asset import Kind, RawAsset, Role


@dataclass(frozen=True)
class SourceCapabilities:
    kinds: frozenset          # Kind.* this source can emit
    roles: frozenset          # Role.* this source can emit
    synthetic_real: str       # "synthetic" | "real"
    has_mixture: bool         # does it provide full mixtures (vs isolated stems only)


@runtime_checkable
class SourceProvider(Protocol):
    id: str                   # stable instance id, e.g. "slakh2100" | "guitarset" | "commission:2026Q1"
    dataset_key: str          # -> training_data registry (license single-source-of-truth)

    def capabilities(self) -> SourceCapabilities: ...
    def iter_assets(self) -> Iterable[RawAsset]: ...
    def health(self) -> bool: ...


_AUDIO_EXTS = (".wav", ".flac", ".aiff", ".aif", ".mp3", ".m4a", ".ogg")


def _first_existing(dir_path: Path, stem: str) -> Path | None:
    for ext in _AUDIO_EXTS:
        p = dir_path / f"{stem}{ext}"
        if p.exists():
            return p
    return None


class SlakhSource:
    """Slakh2100 layout: <root>/<Track#####>/{guitar,mixture,...}.{wav,flac}.
    Emits the guitar STEM (Riley's target) and, when present, the MIXTURE."""
    dataset_key = "slakh2100"

    def __init__(self, root: str | Path, id: str = "slakh2100",
                 synthetic_real: str = "synthetic", emit_mixture: bool = True):
        self.id = id
        self._root = Path(root)
        self._synth = synthetic_real
        self._emit_mixture = emit_mixture

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            kinds=frozenset({Kind.STEM} | ({Kind.MIXTURE} if self._emit_mixture else set())),
            roles=frozenset({Role.GUITAR, Role.MIX}),
            synthetic_real=self._synth, has_mixture=self._emit_mixture)

    def health(self) -> bool:
        return self._root.is_dir()

    def iter_assets(self) -> Iterable[RawAsset]:
        for track_dir in sorted(p for p in self._root.iterdir() if p.is_dir()):
            g = _first_existing(track_dir, "guitar")
            if g is not None:
                yield RawAsset(str(g), kind=Kind.STEM, role=Role.GUITAR,
                               source_tags={"track": track_dir.name, "synthetic_real": self._synth,
                                            "recording_type": "synthetic"})
            if self._emit_mixture:
                m = _first_existing(track_dir, "mixture") or _first_existing(track_dir, "mix")
                if m is not None:
                    yield RawAsset(str(m), kind=Kind.MIXTURE, role=Role.MIX,
                                   source_tags={"track": track_dir.name, "synthetic_real": self._synth,
                                                "recording_type": "synthetic"})


class GuitarSetSource:
    """GuitarSet layout: real solo acoustic guitar. Any *.wav under <root> (mic mix
    or hex) is a guitar STEM. No mixtures (isolated instrument only)."""
    dataset_key = "guitarset"

    def __init__(self, root: str | Path, id: str = "guitarset", glob: str = "*.wav"):
        self.id = id
        self._root = Path(root)
        self._glob = glob

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            kinds=frozenset({Kind.STEM}), roles=frozenset({Role.GUITAR}),
            synthetic_real="real", has_mixture=False)

    def health(self) -> bool:
        return self._root.is_dir()

    # GuitarSet genre codes -> readable regime tags
    _GENRE = {"BN": "bossa_nova", "Funk": "funk", "SS": "singer_songwriter",
              "Rock": "rock", "Jazz": "jazz"}

    def _parse(self, stem: str) -> dict:
        """GuitarSet convention: '<player>_<GEN><take>-<tempo>-<key>_<style>_mic'."""
        tags = {"excerpt": stem, "synthetic_real": "real",
                "recording_type": "acoustic", "guitar_type": "acoustic"}
        parts = stem.split("_")
        if len(parts) >= 2:
            tags["player"] = parts[0]
            seg = parts[1].split("-")
            code = "".join(c for c in seg[0] if c.isalpha())
            tags["genre"] = self._GENRE.get(code, code.lower() or "unknown")
            if len(seg) >= 2 and seg[1].isdigit():
                tags["source_tempo"] = int(seg[1])
            if len(seg) >= 3:
                tags["source_key"] = seg[2]
        if len(parts) >= 3:
            tags["performance_style"] = parts[2]   # comp | solo
        return tags

    def iter_assets(self) -> Iterable[RawAsset]:
        for p in sorted(self._root.rglob(self._glob)):
            if p.suffix.lower() in _AUDIO_EXTS:
                yield RawAsset(str(p), kind=Kind.STEM, role=Role.GUITAR,
                               source_tags=self._parse(p.stem))


# static contract checks — both must satisfy the identical Protocol
_p1: SourceProvider = SlakhSource(".")     # type: ignore[assignment]
_p2: SourceProvider = GuitarSetSource(".")  # type: ignore[assignment]
