"""DatasetRecipe — a named, versioned transformation pipeline, expressed as DATA.

A recipe is an ordered list of (transform_id, params). It is not code: recipes are
declared in the RECIPES registry, versioned, hashable, and recorded into Asset
lineage so any manufactured asset names the exact recipe (and version) that made it.

Recipes compose the M2 transforms via the M2 engine — no new transform machinery.
Real amp/cab profiles (.nam / IR files) slot into a step's params (`model`,
`ir_path`) without changing the recipe abstraction; the built-in NAM model is used
where no profile is supplied (see transforms.NAMTransform).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from .engine import TransformEngine
from .asset import Asset
from .transforms import (GainTransform, EQTransform, IRTransform, NAMTransform,
                         TransformProvider)

# id -> singleton transform instance (the plug-in set a recipe may reference)
TRANSFORM_REGISTRY: dict[str, TransformProvider] = {
    t.id: t for t in (GainTransform(), EQTransform(), IRTransform(), NAMTransform())
}


@dataclass(frozen=True)
class RecipeStep:
    transform_id: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetRecipe:
    name: str
    version: str
    steps: tuple
    description: str = ""
    # descriptive tags stamped onto manufactured assets (regime coverage)
    tags: tuple = ()          # e.g. (("guitar_type","distorted"),("amp","5150"))

    def signature(self) -> str:
        """Stable content hash of the recipe definition (name+version+steps)."""
        payload = json.dumps(
            {"name": self.name, "version": self.version,
             "steps": [(s.transform_id, dict(sorted(s.params.items()))) for s in self.steps]},
            sort_keys=True, default=str)
        return "rcp_" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    @property
    def label(self) -> str:
        return f"{self.name}@{self.version}"


def apply_recipe(engine: TransformEngine, asset: Asset, recipe: DatasetRecipe) -> Asset:
    """Run an asset through every step of a recipe (via the engine's cache), then
    stamp the recipe into lineage + metadata. Returns the final manufactured Asset.
    Deterministic: same asset + same recipe -> same content id (cache-reproducible)."""
    current = asset
    for step in recipe.steps:
        transform = TRANSFORM_REGISTRY[step.transform_id]
        current = engine.run(current, transform, dict(step.params))
    # stamp recipe provenance (same-identity annotation; audio already final)
    md = dict(current.metadata)
    md.update({k: v for k, v in recipe.tags})
    md["recipe"] = recipe.name
    md["recipe_version"] = recipe.version
    return current.evolve(
        stage=f"recipe:{recipe.label}", metadata=md,
        params={"recipe": recipe.name, "version": recipe.version,
                "recipe_signature": recipe.signature()})


# =====================================================================
# The first four production recipes (data, versioned).
# NAM steps use the built-in valve model (model defaults to builtin:tanh_v1);
# real Fender/Marshall/5150 .nam profiles + Mesa/2x12 IRs drop into `model` /
# `ir_path` params with no code change. Diversity here comes from real,
# deterministic gain/EQ/waveshape differences across recipes.
# =====================================================================
RECIPES: dict[str, DatasetRecipe] = {
    "clean_twin": DatasetRecipe(
        name="clean_twin", version="v1",
        description="Clean Fender-twin voicing: light drive, scooped-bright EQ.",
        tags=(("guitar_type", "clean"), ("amp_voicing", "fender_clean")),
        steps=(
            RecipeStep("gain", {"gain_db": -2.0}),
            RecipeStep("nam", {"model": "builtin:tanh_v1", "drive_db": 5.0, "output_db": -3.0, "bias": 0.05}),
            RecipeStep("eq", {"low_db": -1.0, "mid_db": -1.5, "high_db": 2.0}),
        )),
    "british_crunch": DatasetRecipe(
        name="british_crunch", version="v1",
        description="Marshall-style crunch: moderate drive, mid-forward.",
        tags=(("guitar_type", "distorted"), ("amp_voicing", "british_crunch")),
        steps=(
            RecipeStep("gain", {"gain_db": 2.0}),
            RecipeStep("nam", {"model": "builtin:tanh_v1", "drive_db": 15.0, "output_db": -6.0, "bias": 0.12}),
            RecipeStep("eq", {"low_db": 0.0, "mid_db": 3.0, "high_db": -1.0}),
        )),
    "modern_metal": DatasetRecipe(
        name="modern_metal", version="v1",
        description="High-gain 5150-style: heavy drive, tight low end, scooped highs.",
        tags=(("guitar_type", "distorted"), ("amp_voicing", "high_gain")),
        steps=(
            RecipeStep("gain", {"gain_db": 6.0}),
            RecipeStep("nam", {"model": "builtin:tanh_v1", "drive_db": 24.0, "output_db": -8.0, "bias": 0.30}),
            RecipeStep("eq", {"low_db": 2.0, "mid_db": -2.0, "high_db": -4.0}),
        )),
    "acoustic_natural": DatasetRecipe(
        name="acoustic_natural", version="v1",
        description="Minimal processing: gentle presence lift, no amp.",
        tags=(("guitar_type", "acoustic"), ("amp_voicing", "none")),
        steps=(
            RecipeStep("eq", {"mid_db": 1.0, "high_db": 1.5}),
        )),
}


def all_recipes() -> list:
    return list(RECIPES.values())
