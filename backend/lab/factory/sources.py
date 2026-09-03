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
from typing import Iterable, Optional, Protocol, runtime_checkable

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


def _is_junk(p: Path) -> bool:
    """AppleDouble / macOS-zip resource-fork artifacts — not real audio."""
    return p.name.startswith("._") or "__MACOSX" in p.parts


def _wavs(root: Path, glob: str = "*.wav"):
    for p in sorted(root.rglob(glob)):
        if p.suffix.lower() in _AUDIO_EXTS and not _is_junk(p):
            yield p


def _first_existing(dir_path: Path, stem: str) -> Path | None:
    for ext in _AUDIO_EXTS:
        p = dir_path / f"{stem}{ext}"
        if p.exists():
            return p
    return None


class SlakhSource:
    """Slakh2100 layout: <root>/<Track#####>/{guitar,synth,mixture,...}.{wav,flac}.
    Emits one target STEM per track and, when present, the MIXTURE.

    ``stem_role`` selects which target to read. It defaults to guitar —
    Riley's original target — so existing callers are unchanged. Slakh is
    rendered from MIDI against General MIDI instrument classes, so its
    per-instrument stems are exactly labelled by construction, which makes
    it the one registered dataset that can supply abundant synth targets
    with trustworthy ground truth (CC BY 4.0, see training_data).

    The stem FILENAME is taken from the role by default and overridable via
    ``stem_filename``, because the on-disk name depends on how a given Slakh
    checkout was flattened — this class never had a way to express that.
    """
    dataset_key = "slakh2100"

    def __init__(self, root: str | Path, id: str = "slakh2100",
                 synthetic_real: str = "synthetic", emit_mixture: bool = True,
                 stem_role: str = Role.GUITAR, stem_filename: str | None = None):
        self.id = id
        self._root = Path(root)
        self._synth = synthetic_real
        self._emit_mixture = emit_mixture
        self._stem_role = stem_role
        self._stem_filename = stem_filename or stem_role

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            kinds=frozenset({Kind.STEM} | ({Kind.MIXTURE} if self._emit_mixture else set())),
            roles=frozenset({self._stem_role, Role.MIX}),
            synthetic_real=self._synth, has_mixture=self._emit_mixture)

    def health(self) -> bool:
        return self._root.is_dir()

    def iter_assets(self) -> Iterable[RawAsset]:
        for track_dir in sorted(p for p in self._root.iterdir() if p.is_dir()):
            g = _first_existing(track_dir, self._stem_filename)
            if g is not None:
                yield RawAsset(str(g), kind=Kind.STEM, role=self._stem_role,
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
        for p in _wavs(self._root, self._glob):
            yield RawAsset(str(p), kind=Kind.STEM, role=Role.GUITAR,
                           source_tags=self._parse(p.stem))


class GuitarTechsSource:
    """Guitar-TECHS: real ELECTRIC guitar. Layout
    <P#>_<content>/audio/<perspective>/<perspective>_<label>.wav
    (perspective = micamp | di | mic...). Fills the acoustic->electric gap + real
    playing-technique coverage (bends/harmonics/palm-mute/vibrato/...)."""
    dataset_key = "guitar_techs"
    _PERSP = {"micamp": "amp_mic", "directinput": "di", "di": "di", "mic": "room_mic",
              "ego": "ego_mic", "exo": "exo_mic"}

    def __init__(self, root: str | Path, id: str = "guitar_techs"):
        self.id = id
        self._root = Path(root)

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(kinds=frozenset({Kind.STEM}), roles=frozenset({Role.GUITAR}),
                                  synthetic_real="real", has_mixture=False)

    def health(self) -> bool:
        return self._root.is_dir()

    def _parse(self, p: Path) -> dict:
        parts = p.parts
        tags = {"synthetic_real": "real", "guitar_type": "electric",
                "recording_type": "electric"}
        # find "<P#>_<content>" segment + perspective dir
        for seg in parts:
            if seg[:1] == "P" and "_" in seg:
                pl, _, content = seg.partition("_")
                tags["player"] = pl
                tags["content"] = content
        persp = p.parent.name
        tags["recording_type"] = self._PERSP.get(persp, persp)
        label = p.stem.split("_", 1)[-1]
        if tags.get("content") == "techniques":
            tags["performance_style"] = label      # Bendings/Harmonics/PalmMute/...
        elif tags.get("content"):
            tags["performance_style"] = tags["content"]
        tags["excerpt"] = p.stem
        return tags

    def iter_assets(self) -> Iterable[RawAsset]:
        for p in _wavs(self._root):
            yield RawAsset(str(p), kind=Kind.STEM, role=Role.GUITAR, source_tags=self._parse(p))


class EGFxSetSource:
    """EGFxSet: real electric-guitar notes through real hardware, layout
    <Effect>/<Pickup>/<string>-<fret>.wav. GROUND-TRUTH pickup + effect -> fills the
    pickup + gain/distortion gaps. Isolated notes (augmentation/timbre bank)."""
    dataset_key = "egfxset"
    _DISTORTION = {"rat", "tubescreamer", "tube screamer", "bluesdriver", "blues driver",
                   "distortion", "overdrive", "fuzz"}

    def __init__(self, root: str | Path, id: str = "egfxset"):
        self.id = id
        self._root = Path(root)

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(kinds=frozenset({Kind.STEM}), roles=frozenset({Role.GUITAR}),
                                  synthetic_real="real", has_mixture=False)

    def health(self) -> bool:
        return self._root.is_dir()

    def _parse(self, p: Path) -> dict:
        # .../<Effect>/<Pickup>/<string>-<fret>.wav
        pickup = p.parent.name
        effect = p.parent.parent.name
        distorted = effect.lower().replace("-", "").replace("_", "") in \
            {e.replace(" ", "") for e in self._DISTORTION}
        return {"synthetic_real": "real", "recording_type": "di_processed",
                "guitar_type": "distorted" if distorted else "clean",
                "gain": 0.8 if distorted else 0.05,
                "pickup": pickup.lower(), "effect": effect,
                "note": p.stem, "excerpt": f"{effect}_{pickup}_{p.stem}"}

    def iter_assets(self) -> Iterable[RawAsset]:
        for p in _wavs(self._root):
            yield RawAsset(str(p), kind=Kind.STEM, role=Role.GUITAR, source_tags=self._parse(p))


class DirectorySource:
    """Generic role-tagged directory source: every audio file under <root> becomes an
    asset with the given role + dataset_key (+ optional fixed tags). Used for backing
    stem pools (drums/bass/vocals/keys/...) and any flat licensed drop. Per-file
    attribution sidecars (<id>.json with license/user) are folded into source_tags."""
    def __init__(self, root: str | Path, *, role: str, dataset_key: str, kind: str = Kind.STEM,
                 id: Optional[str] = None, tags: Optional[dict] = None):
        self.id = id or f"dir:{dataset_key}:{role}"
        self.dataset_key = dataset_key
        self._root = Path(root)
        self._role = role
        self._kind = kind
        self._tags = tags or {}

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(kinds=frozenset({self._kind}), roles=frozenset({self._role}),
                                  synthetic_real="real", has_mixture=False)

    def health(self) -> bool:
        return self._root.is_dir()

    def iter_assets(self) -> Iterable[RawAsset]:
        import json as _json
        for p in _wavs(self._root, "*"):
            tags = {"synthetic_real": "real", "role": self._role, **self._tags}
            side = p.with_suffix(".json")
            if side.exists():
                try:
                    m = _json.loads(side.read_text())
                    tags["attribution"] = m.get("user") or m.get("name")
                    tags["source_license"] = m.get("license")
                except Exception:
                    pass
            yield RawAsset(str(p), kind=self._kind, role=self._role, source_tags=tags)


# static contract checks — all must satisfy the identical Protocol
_p1: SourceProvider = SlakhSource(".")      # type: ignore[assignment]
_p5: SourceProvider = DirectorySource(".", role=Role.DRUMS, dataset_key="x")  # type: ignore[assignment]
_p2: SourceProvider = GuitarSetSource(".")   # type: ignore[assignment]
_p3: SourceProvider = GuitarTechsSource(".")  # type: ignore[assignment]
_p4: SourceProvider = EGFxSetSource(".")      # type: ignore[assignment]
