"""Scenario — a recording SITUATION expressed as data (not a genre, not a mixer).

A Scenario declares the roles present in a mix, their levels/pan, how hard the
maskers sit over the guitar (the difficulty knob), and light bus treatment. It is
versioned + hashable so a generated mixture names the exact situation that made it.

Real backing pools plug in via the ScenarioProvider (backing.py) with zero change
to these specs — a Scenario describes WHAT the situation is; the provider supplies
the audio for each role.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoleSpec:
    role: str            # "vocals" | "drums" | "bass" | "keys" | "synth" | "percussion"
    level_db: float = -6.0
    pan: float = 0.0     # -1 (L) .. +1 (R)
    is_masker: bool = False   # sits over the guitar; masking_db is applied on top
    voice: str = ""      # generator hint, e.g. "male"/"female"/"scream"/"upright"/"brushes"/"pad"


@dataclass(frozen=True)
class Scenario:
    name: str
    version: str
    roles: tuple                      # tuple[RoleSpec]
    guitar_level_db: float = -3.0
    guitar_pan: float = 0.0
    masking_db: float = 0.0           # extra level added to maskers vs guitar (difficulty)
    compression: float = 0.0          # 0..1 bus soft-compression amount
    stereo: bool = True
    benchmark_regime: str = ""        # the failure regime this scenario manufactures
    description: str = ""

    def signature(self) -> str:
        payload = json.dumps({
            "name": self.name, "version": self.version,
            "roles": [(r.role, r.level_db, r.pan, r.is_masker, r.voice) for r in self.roles],
            "guitar_level_db": self.guitar_level_db, "guitar_pan": self.guitar_pan,
            "masking_db": self.masking_db, "compression": self.compression,
            "stereo": self.stereo,
        }, sort_keys=True)
        return "scn_" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    @property
    def label(self) -> str:
        return f"{self.name}@{self.version}"


# =====================================================================
# The first five recording situations (data, versioned).
# =====================================================================
SCENARIOS: dict = {
    "classic_rock": Scenario(
        name="classic_rock", version="v1", benchmark_regime="clean_electric_under_vocal",
        description="Male vocal, live drums, bass, rhythm guitar. Medium masking, medium comp.",
        guitar_level_db=-4.0, guitar_pan=-0.2, masking_db=3.0, compression=0.3,
        roles=(RoleSpec("vocals", -3.0, 0.0, is_masker=True, voice="male"),
               RoleSpec("drums", -6.0, 0.0, voice="live"),
               RoleSpec("bass", -7.0, 0.0, voice="electric"))),
    "acoustic_vocal": Scenario(
        name="acoustic_vocal", version="v1", benchmark_regime="acoustic_under_vocal",
        description="Female vocal, acoustic guitar, upright bass, brushes. Sparse, light room.",
        guitar_level_db=-3.0, guitar_pan=0.1, masking_db=2.0, compression=0.1,
        roles=(RoleSpec("vocals", -4.0, 0.0, is_masker=True, voice="female"),
               RoleSpec("bass", -9.0, -0.1, voice="upright"),
               RoleSpec("percussion", -12.0, 0.2, voice="brushes"))),
    "modern_metal": Scenario(
        name="modern_metal", version="v1", benchmark_regime="heavy_masking",
        description="Dense kick, triggered snare, bass, synth, scream, double rhythm gtr. Extreme masking.",
        guitar_level_db=-5.0, guitar_pan=-0.3, masking_db=6.0, compression=0.6,
        roles=(RoleSpec("vocals", -2.0, 0.0, is_masker=True, voice="scream"),
               RoleSpec("drums", -3.0, 0.0, voice="triggered"),
               RoleSpec("bass", -5.0, 0.0, voice="electric"),
               RoleSpec("synth", -8.0, 0.3, is_masker=True, voice="pad"))),
    "pop": Scenario(
        name="pop", version="v1", benchmark_regime="dense_synth_masking",
        description="Layered synths, clean electric, compressed vocal, bright mix.",
        guitar_level_db=-6.0, guitar_pan=0.25, masking_db=4.0, compression=0.5,
        roles=(RoleSpec("vocals", -2.5, 0.0, is_masker=True, voice="pop"),
               RoleSpec("synth", -5.0, -0.3, is_masker=True, voice="pad"),
               RoleSpec("drums", -6.0, 0.0, voice="electronic"),
               RoleSpec("bass", -7.0, 0.0, voice="synth"))),
    "jazz_trio": Scenario(
        name="jazz_trio", version="v1", benchmark_regime="sparse_clean",
        description="Upright bass, brushes, piano, room. Sparse, dynamic.",
        guitar_level_db=-3.0, guitar_pan=-0.1, masking_db=0.0, compression=0.1,
        roles=(RoleSpec("bass", -7.0, -0.2, voice="upright"),
               RoleSpec("percussion", -11.0, 0.3, voice="brushes"),
               RoleSpec("keys", -8.0, 0.2, voice="piano"))),
}


def all_scenarios() -> list:
    return list(SCENARIOS.values())
