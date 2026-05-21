"""Plugin-to-block mapper for ToneForge.

Maps discovered plugins to ToneForge block families based on:
- Plugin name analysis
- Manufacturer matching
- Category inference
- User-defined mappings

Supports both automatic and user-defined mappings.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


@dataclass
class BlockMapping:
    """Mapping from a plugin to ToneForge block."""

    plugin_id: str
    block_family: str
    block_type: str  # "amp", "cab", "effect"
    confidence: float  # 0.0 to 1.0
    match_reason: str  # Why this mapping was chosen
    is_user_defined: bool = False

    # Optional parameter mappings
    param_mappings: Dict[str, str] = field(default_factory=dict)


# Known manufacturer mappings
MANUFACTURER_MAPPINGS = {
    # Amp plugin manufacturers
    "neural dsp": {
        "fortin": "fortin_satan",
        "soldano": "soldano_slo",
        "omega": "mesa_rectifier",
        "nameless": "5150_peavey",
        "gojira": "mesa_rectifier",
        "archetype": "high_gain_modern",
        "plini": "high_gain_modern",
        "cory wong": "fender_clean",
        "tim henson": "high_gain_modern",
        "rabea massaad": "5150_peavey",
        "nolly": "mesa_rectifier",
    },
    "line 6": {
        "helix": "generic",
        "pod": "generic",
    },
    "ik multimedia": {
        "amplitube": "generic",
        "tonex": "generic",
    },
    "positive grid": {
        "bias amp": "generic",
        "bias fx": "generic",
        "spark": "generic",
    },
    "universal audio": {
        "marshall": "marshall_jcm",
        "fender": "fender_clean",
        "mesa": "mesa_rectifier",
        "friedman": "friedman_be",
    },
    "mercuriall": {
        "spark": "friedman_be",
        "reaxis": "mesa_rectifier",
        "euphoria": "dumble_ods",
        "u530": "engl_e530",
    },
    "stl tones": {
        "tonehub": "generic",
        "amphub": "generic",
    },
    "brainworx": {
        "marshall": "marshall_jcm",
        "fender": "fender_clean",
        "mesa": "mesa_rectifier",
    },
}

# Amp name patterns to block family
AMP_PATTERNS = {
    # Marshall family
    r"marshall|plexi|jcm|jmp|jubilee|2203|1959|800|900": "marshall_jcm",
    r"jcm800|jcm 800": "marshall_jcm800",
    r"plexi|1959|super.?lead": "marshall_plexi",

    # Fender family
    r"fender|twin|deluxe|bassman|princeton|champ|vibrolux": "fender_clean",
    r"twin.?reverb": "fender_twin",
    r"deluxe.?reverb": "fender_deluxe",

    # Mesa/Boogie family
    r"mesa|boogie|rectifier|dual.?rec|triple.?rec|mark|road.?king": "mesa_rectifier",
    r"mark.?(ii|2|iii|3|iv|4|v|5)": "mesa_mark",

    # Peavey/EVH family
    r"5150|evh|peavey|6505|5153": "5150_peavey",

    # Vox family
    r"vox|ac.?15|ac.?30|ac15|ac30": "vox_ac",

    # Soldano
    r"soldano|slo|slo.?100": "soldano_slo",

    # Friedman
    r"friedman|be.?100|be100|dirty.?shirley": "friedman_be",

    # Engl
    r"engl|savage|fireball|powerball|invader": "engl_savage",

    # Orange
    r"orange|rockerverb|thunderverb|or.?15|or15|terror": "orange_rockerverb",

    # Bogner
    r"bogner|ecstasy|uberschall|shiva": "bogner_ecstasy",

    # Diezel
    r"diezel|herbert|vh4|hagen": "diezel_herbert",

    # Dumble
    r"dumble|overdrive.?special|ods|two.?rock|bludotone": "dumble_ods",

    # Fortin
    r"fortin|natas|cali|sigil|satan": "fortin_satan",

    # High gain generic
    r"high.?gain|metal|djent|brutal|modern": "high_gain_modern",

    # Clean/jazz
    r"jazz|clean|acoustic|roland.?jc|jc.?120": "clean_jazz",
}

# Cabinet patterns
CAB_PATTERNS = {
    r"4x12|412": "4x12",
    r"2x12|212": "2x12",
    r"1x12|112": "1x12",
    r"1x10|110": "1x10",
    r"v30|vintage.?30": "4x12_v30",
    r"greenback|g12m|g12h": "4x12_greenback",
    r"celestion": "celestion",
    r"ir|impulse|cabinet|cab": "ir_loader",
}

# Effect patterns
EFFECT_PATTERNS = {
    # Distortion/Overdrive
    r"tube.?screamer|ts.?9|ts9|ts808|ts.?808|ibanez": "overdrive_ts",
    r"klon|centaur|klone": "overdrive_klon",
    r"blues.?driver|bd.?2|bd2": "overdrive_blues",
    r"rat|proco|pro.?co": "distortion_rat",
    r"big.?muff|muff|fuzz": "fuzz_muff",
    r"fuzz.?face|fuzzface|germanium": "fuzz_face",
    r"overdrive|od|drive|boost": "overdrive_generic",
    r"distortion|dist": "distortion_generic",
    r"metal.?zone|mz|hm.?2|hm2": "distortion_metal",

    # Modulation
    r"chorus|ce.?1|ce.?2|dimension": "chorus",
    r"flanger|bf.?2|electric.?mistress": "flanger",
    r"phaser|phase.?90|small.?stone": "phaser",
    r"tremolo|trem|vibrato": "tremolo",
    r"uni.?vibe|univibe|vibe": "univibe",
    r"rotary|leslie": "rotary",

    # Delay
    r"delay|echo|dd.?[0-9]|dm.?2": "delay",
    r"tape.?delay|tape.?echo|space.?echo|re.?201": "delay_tape",
    r"analog.?delay|bucket.?brigade|bbd": "delay_analog",
    r"digital.?delay": "delay_digital",
    r"ping.?pong": "delay_pingpong",

    # Reverb
    r"reverb|verb|rv.?[0-9]|hall|plate|room|spring": "reverb",
    r"spring.?reverb": "reverb_spring",
    r"plate": "reverb_plate",
    r"hall": "reverb_hall",
    r"shimmer": "reverb_shimmer",

    # Dynamics
    r"compressor|comp|dyna|squeeze": "compressor",
    r"noise.?gate|gate|suppressor": "noise_gate",
    r"limiter": "limiter",

    # EQ
    r"eq|equalizer|equaliser|graphic|parametric": "eq",
    r"graphic.?eq": "eq_graphic",
    r"parametric": "eq_parametric",

    # Pitch
    r"pitch|whammy|harmonizer|harmony|octave": "pitch_shifter",
    r"whammy": "pitch_whammy",
    r"octave|octaver": "pitch_octave",

    # Filter
    r"wah|cry.?baby|crybaby": "wah",
    r"auto.?wah|envelope|filter": "filter_auto",

    # Utility
    r"tuner": "tuner",
    r"looper|loop": "looper",
    r"volume|vol": "volume",
}


class PluginMapper:
    """Maps plugins to ToneForge blocks."""

    def __init__(self, db=None):
        """Initialize the mapper.

        Args:
            db: Optional PluginDatabase instance for caching mappings
        """
        self.db = db

    def map_plugin(
        self,
        plugin_id: str,
        name: str,
        manufacturer: str,
        categories: List[str],
        plugin_type: str,
    ) -> Optional[BlockMapping]:
        """Map a plugin to a ToneForge block.

        Args:
            plugin_id: Unique plugin identifier
            name: Plugin display name
            manufacturer: Plugin manufacturer
            categories: Plugin categories
            plugin_type: "effect", "instrument", etc.

        Returns:
            BlockMapping if found, None otherwise
        """
        # Check for cached user-defined mapping
        if self.db:
            cached = self.db.get_block_mapping(plugin_id)
            if cached and cached.get('is_user_defined'):
                return BlockMapping(
                    plugin_id=plugin_id,
                    block_family=cached['block_family'],
                    block_type=cached['block_type'],
                    confidence=cached['confidence'],
                    match_reason="User-defined mapping",
                    is_user_defined=True,
                )

        # Try manufacturer-specific mapping
        mapping = self._map_by_manufacturer(plugin_id, name, manufacturer)
        if mapping:
            return mapping

        # Try name pattern matching
        mapping = self._map_by_name(plugin_id, name, categories)
        if mapping:
            return mapping

        # Try category-based mapping
        mapping = self._map_by_category(plugin_id, name, categories)
        if mapping:
            return mapping

        return None

    def _map_by_manufacturer(
        self,
        plugin_id: str,
        name: str,
        manufacturer: str,
    ) -> Optional[BlockMapping]:
        """Try to map plugin by manufacturer.

        Args:
            plugin_id: Plugin identifier
            name: Plugin name
            manufacturer: Manufacturer name

        Returns:
            BlockMapping if found
        """
        manufacturer_lower = manufacturer.lower()
        name_lower = name.lower()

        for mfr, mappings in MANUFACTURER_MAPPINGS.items():
            if mfr in manufacturer_lower:
                # Check if plugin name matches any known products
                for product, block_family in mappings.items():
                    if product in name_lower:
                        return BlockMapping(
                            plugin_id=plugin_id,
                            block_family=block_family,
                            block_type=self._infer_block_type(block_family, name_lower),
                            confidence=0.9,
                            match_reason=f"Manufacturer match: {manufacturer} - {product}",
                        )

        return None

    def _map_by_name(
        self,
        plugin_id: str,
        name: str,
        categories: List[str],
    ) -> Optional[BlockMapping]:
        """Try to map plugin by name pattern.

        Args:
            plugin_id: Plugin identifier
            name: Plugin name
            categories: Plugin categories

        Returns:
            BlockMapping if found
        """
        name_lower = name.lower()

        # Check amp patterns
        for pattern, block_family in AMP_PATTERNS.items():
            if re.search(pattern, name_lower, re.IGNORECASE):
                return BlockMapping(
                    plugin_id=plugin_id,
                    block_family=block_family,
                    block_type="amp",
                    confidence=0.8,
                    match_reason=f"Amp name match: {pattern}",
                )

        # Check cabinet patterns
        for pattern, block_family in CAB_PATTERNS.items():
            if re.search(pattern, name_lower, re.IGNORECASE):
                return BlockMapping(
                    plugin_id=plugin_id,
                    block_family=block_family,
                    block_type="cab",
                    confidence=0.75,
                    match_reason=f"Cabinet name match: {pattern}",
                )

        # Check effect patterns
        for pattern, block_family in EFFECT_PATTERNS.items():
            if re.search(pattern, name_lower, re.IGNORECASE):
                return BlockMapping(
                    plugin_id=plugin_id,
                    block_family=block_family,
                    block_type="effect",
                    confidence=0.7,
                    match_reason=f"Effect name match: {pattern}",
                )

        return None

    def _map_by_category(
        self,
        plugin_id: str,
        name: str,
        categories: List[str],
    ) -> Optional[BlockMapping]:
        """Try to map plugin by category.

        Args:
            plugin_id: Plugin identifier
            name: Plugin name
            categories: Plugin categories

        Returns:
            BlockMapping if found
        """
        categories_lower = [c.lower() for c in categories]

        category_to_block = {
            "amp": ("amp_generic", "amp"),
            "amplifier": ("amp_generic", "amp"),
            "preamp": ("preamp_generic", "amp"),
            "cabinet": ("cab_generic", "cab"),
            "cab": ("cab_generic", "cab"),
            "ir": ("ir_loader", "cab"),
            "distortion": ("distortion_generic", "effect"),
            "overdrive": ("overdrive_generic", "effect"),
            "fuzz": ("fuzz_generic", "effect"),
            "delay": ("delay", "effect"),
            "reverb": ("reverb", "effect"),
            "chorus": ("chorus", "effect"),
            "flanger": ("flanger", "effect"),
            "phaser": ("phaser", "effect"),
            "modulation": ("modulation_generic", "effect"),
            "eq": ("eq", "effect"),
            "compressor": ("compressor", "effect"),
            "dynamics": ("dynamics_generic", "effect"),
            "filter": ("filter_generic", "effect"),
            "wah": ("wah", "effect"),
            "pitch": ("pitch_shifter", "effect"),
        }

        for cat in categories_lower:
            if cat in category_to_block:
                block_family, block_type = category_to_block[cat]
                return BlockMapping(
                    plugin_id=plugin_id,
                    block_family=block_family,
                    block_type=block_type,
                    confidence=0.5,
                    match_reason=f"Category match: {cat}",
                )

        return None

    def _infer_block_type(self, block_family: str, name: str) -> str:
        """Infer block type from family and name.

        Args:
            block_family: Block family name
            name: Plugin name

        Returns:
            Block type string
        """
        if any(x in block_family for x in ["amp", "gain", "marshall", "fender", "mesa", "5150", "vox", "soldano", "friedman", "engl", "orange", "bogner", "diezel", "dumble", "fortin", "clean", "jazz"]):
            return "amp"

        if any(x in block_family for x in ["cab", "ir", "412", "212", "112", "v30", "greenback", "celestion"]):
            return "cab"

        return "effect"

    def map_plugins(self, plugins: list) -> List[Tuple[Any, Optional[BlockMapping]]]:
        """Map multiple plugins.

        Args:
            plugins: List of PluginInfo or dicts with plugin data

        Returns:
            List of (plugin, mapping) tuples
        """
        results = []

        for plugin in plugins:
            # Handle both PluginInfo objects and dicts
            if hasattr(plugin, 'plugin_id'):
                plugin_id = plugin.plugin_id
                name = plugin.name
                manufacturer = plugin.manufacturer
                categories = plugin.categories
                plugin_type = plugin.plugin_type
            else:
                plugin_id = plugin['plugin_id']
                name = plugin['name']
                manufacturer = plugin['manufacturer']
                categories = plugin.get('categories', [])
                plugin_type = plugin.get('plugin_type', 'effect')

            mapping = self.map_plugin(
                plugin_id=plugin_id,
                name=name,
                manufacturer=manufacturer,
                categories=categories,
                plugin_type=plugin_type,
            )

            results.append((plugin, mapping))

            # Cache mapping if database available
            if mapping and self.db:
                self.db.set_block_mapping(
                    plugin_id=mapping.plugin_id,
                    block_family=mapping.block_family,
                    block_type=mapping.block_type,
                    confidence=mapping.confidence,
                    is_user_defined=mapping.is_user_defined,
                )

        return results

    def get_plugins_for_descriptor(
        self,
        descriptor: Dict[str, Any],
        all_plugins: list,
    ) -> Dict[str, List[Tuple[Any, BlockMapping]]]:
        """Find plugins that could recreate a tone descriptor.

        Args:
            descriptor: ToneDescriptor as dict
            all_plugins: List of available plugins

        Returns:
            Dict mapping slot (amp, cab, effects) to list of (plugin, mapping)
        """
        recommendations = {
            "amp": [],
            "cab": [],
            "effects": [],
        }

        # Map all plugins
        mapped = self.map_plugins(all_plugins)

        # Get target families from descriptor
        amp_family = descriptor.get("amp", {}).get("family", "")
        cab_config = descriptor.get("cab", {}).get("configuration", "")

        for plugin, mapping in mapped:
            if mapping is None:
                continue

            if mapping.block_type == "amp":
                # Check if amp family matches
                if amp_family and amp_family in mapping.block_family:
                    recommendations["amp"].append((plugin, mapping))
                elif not amp_family:
                    recommendations["amp"].append((plugin, mapping))

            elif mapping.block_type == "cab":
                # Check if cab config matches
                if cab_config and cab_config in mapping.block_family:
                    recommendations["cab"].append((plugin, mapping))
                elif not cab_config:
                    recommendations["cab"].append((plugin, mapping))

            elif mapping.block_type == "effect":
                recommendations["effects"].append((plugin, mapping))

        # Sort by confidence
        for slot in recommendations:
            recommendations[slot].sort(
                key=lambda x: x[1].confidence,
                reverse=True,
            )

        return recommendations


def get_mapper(db=None) -> PluginMapper:
    """Get a plugin mapper instance.

    Args:
        db: Optional PluginDatabase instance

    Returns:
        PluginMapper instance
    """
    return PluginMapper(db)


def map_plugin(
    plugin_id: str,
    name: str,
    manufacturer: str,
    categories: List[str] = None,
    plugin_type: str = "effect",
) -> Optional[BlockMapping]:
    """Convenience function to map a single plugin.

    Args:
        plugin_id: Unique plugin identifier
        name: Plugin display name
        manufacturer: Plugin manufacturer
        categories: Plugin categories
        plugin_type: "effect", "instrument", etc.

    Returns:
        BlockMapping if found, None otherwise
    """
    mapper = PluginMapper()
    return mapper.map_plugin(
        plugin_id=plugin_id,
        name=name,
        manufacturer=manufacturer,
        categories=categories or [],
        plugin_type=plugin_type,
    )
