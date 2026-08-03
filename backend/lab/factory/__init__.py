"""Riley Data Factory (see docs/RILEY_DATA_FACTORY.md).

Milestone 1 — the spine: Asset · SourceProvider · AssetCatalog · PipelineRunner.
Every asset enters through one abstraction and travels source -> license -> audit
-> catalog as immutable, provenance-tracked records. Augmentation / mix generation
/ coverage / commissioning / GPU are later milestones and deliberately absent here.
"""
from .asset import (
    Asset,
    RawAsset,
    LicenseStamp,
    Kind,
    Role,
    Status,
    content_hash_file,
)
from .sources import SourceProvider, SourceCapabilities, SlakhSource, GuitarSetSource
from .catalog import AssetCatalog
from .pipeline import PipelineRunner
from .transforms import (
    TransformProvider, GainTransform, EQTransform, IRTransform, NAMTransform,
)
from .engine import TransformEngine, EngineStats
from .recipes import DatasetRecipe, RecipeStep, RECIPES, all_recipes, apply_recipe, TRANSFORM_REGISTRY
from .manufacture import manufacture, build_manufactured_manifest, ManufactureReport
from .scenario import Scenario, RoleSpec, SCENARIOS, all_scenarios
from .backing import ScenarioProvider, SyntheticBackingProvider, StemPoolBackingProvider
from .studio import VirtualStudio, StudioReport, build_supervised_manifest
from .coverage import coverage_report, render_report, DIMENSIONS

__all__ = [
    "Asset", "RawAsset", "LicenseStamp", "Kind", "Role", "Status", "content_hash_file",
    "SourceProvider", "SourceCapabilities", "SlakhSource", "GuitarSetSource",
    "AssetCatalog", "PipelineRunner",
    "TransformProvider", "GainTransform", "EQTransform", "IRTransform", "NAMTransform",
    "TransformEngine", "EngineStats",
    "DatasetRecipe", "RecipeStep", "RECIPES", "all_recipes", "apply_recipe", "TRANSFORM_REGISTRY",
    "manufacture", "build_manufactured_manifest", "ManufactureReport",
    "Scenario", "RoleSpec", "SCENARIOS", "all_scenarios",
    "ScenarioProvider", "SyntheticBackingProvider", "StemPoolBackingProvider",
    "VirtualStudio", "StudioReport", "build_supervised_manifest",
    "coverage_report", "render_report", "DIMENSIONS",
]
