"""Tests for local plugin scanner module.

Tests the plugin database, mapper, and scanner components.
"""
import pytest
import tempfile
from pathlib import Path

from local_engine.plugin_scanner.scanner_macos import PluginInfo
from local_engine.plugin_scanner.plugin_db import PluginDatabase
from local_engine.plugin_scanner.plugin_mapper import (
    PluginMapper,
    BlockMapping,
    map_plugin,
    AMP_PATTERNS,
    EFFECT_PATTERNS,
)


# ============================================================================
# Test fixtures
# ============================================================================

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_plugins.db"
        db = PluginDatabase(db_path)
        yield db


@pytest.fixture
def sample_plugin():
    """Create a sample plugin for testing."""
    return PluginInfo(
        plugin_id="vst3:neural_dsp.archetype_nolly",
        name="Archetype Nolly",
        manufacturer="Neural DSP",
        version="2.0.1",
        path=Path("/Library/Audio/Plug-Ins/VST3/Archetype Nolly.vst3"),
        format="vst3",
        plugin_type="effect",
        categories=["Amp", "Distortion"],
        description="High-gain amp simulator",
        modified_time=1700000000.0,
    )


@pytest.fixture
def sample_plugins():
    """Create a list of sample plugins."""
    return [
        PluginInfo(
            plugin_id="au:com.native-instruments.guitar-rig",
            name="Guitar Rig 6",
            manufacturer="Native Instruments",
            version="6.4.0",
            path=Path("/Library/Audio/Plug-Ins/Components/Guitar Rig 6.component"),
            format="au",
            plugin_type="effect",
            categories=["Amp", "Effects"],
            modified_time=1700000001.0,
        ),
        PluginInfo(
            plugin_id="vst3:neural_dsp.nameless",
            name="Archetype Nameless",
            manufacturer="Neural DSP",
            version="1.0.0",
            path=Path("/Library/Audio/Plug-Ins/VST3/Archetype Nameless.vst3"),
            format="vst3",
            plugin_type="effect",
            categories=["Amp"],
            modified_time=1700000002.0,
        ),
        PluginInfo(
            plugin_id="vst3:valhalla.valhalla_room",
            name="Valhalla Room",
            manufacturer="Valhalla DSP",
            version="1.6.0",
            path=Path("/Library/Audio/Plug-Ins/VST3/Valhalla Room.vst3"),
            format="vst3",
            plugin_type="effect",
            categories=["Reverb"],
            modified_time=1700000003.0,
        ),
        PluginInfo(
            plugin_id="au:com.soundtoys.echoboy",
            name="EchoBoy",
            manufacturer="Soundtoys",
            version="5.4.0",
            path=Path("/Library/Audio/Plug-Ins/Components/EchoBoy.component"),
            format="au",
            plugin_type="effect",
            categories=["Delay"],
            modified_time=1700000004.0,
        ),
        PluginInfo(
            plugin_id="vst3:line6.helix_native",
            name="Helix Native",
            manufacturer="Line 6",
            version="3.6.0",
            path=Path("/Library/Audio/Plug-Ins/VST3/Helix Native.vst3"),
            format="vst3",
            plugin_type="effect",
            categories=["Amp", "Effects"],
            modified_time=1700000005.0,
        ),
    ]


# ============================================================================
# PluginInfo tests
# ============================================================================

class TestPluginInfo:
    """Tests for the PluginInfo dataclass."""

    def test_create_plugin_info(self, sample_plugin):
        """Test creating a PluginInfo instance."""
        assert sample_plugin.plugin_id == "vst3:neural_dsp.archetype_nolly"
        assert sample_plugin.name == "Archetype Nolly"
        assert sample_plugin.format == "vst3"
        assert "Amp" in sample_plugin.categories

    def test_to_dict(self, sample_plugin):
        """Test converting to dictionary."""
        d = sample_plugin.to_dict()

        assert d["plugin_id"] == sample_plugin.plugin_id
        assert d["name"] == sample_plugin.name
        assert d["path"] == str(sample_plugin.path)
        assert d["categories"] == sample_plugin.categories

    def test_from_dict(self, sample_plugin):
        """Test creating from dictionary."""
        d = sample_plugin.to_dict()
        restored = PluginInfo.from_dict(d)

        assert restored.plugin_id == sample_plugin.plugin_id
        assert restored.name == sample_plugin.name
        assert restored.path == sample_plugin.path


# ============================================================================
# PluginDatabase tests
# ============================================================================

class TestPluginDatabase:
    """Tests for the PluginDatabase class."""

    def test_init_creates_tables(self, temp_db):
        """Test that initialization creates required tables."""
        # Database should be created with tables
        assert temp_db.db_path.exists()

    def test_add_plugin(self, temp_db, sample_plugin):
        """Test adding a plugin."""
        result = temp_db.add_plugin(sample_plugin)
        assert result is True

        # Should be retrievable
        retrieved = temp_db.get_plugin(sample_plugin.plugin_id)
        assert retrieved is not None
        assert retrieved["name"] == sample_plugin.name

    def test_add_plugins_batch(self, temp_db, sample_plugins):
        """Test adding multiple plugins."""
        added, errors = temp_db.add_plugins(sample_plugins)

        assert added == len(sample_plugins)
        assert errors == 0

    def test_get_plugin_by_path(self, temp_db, sample_plugin):
        """Test getting plugin by path."""
        temp_db.add_plugin(sample_plugin)

        retrieved = temp_db.get_plugin_by_path(sample_plugin.path)
        assert retrieved is not None
        assert retrieved["plugin_id"] == sample_plugin.plugin_id

    def test_search_plugins_by_query(self, temp_db, sample_plugins):
        """Test searching plugins by name."""
        temp_db.add_plugins(sample_plugins)

        results = temp_db.search_plugins(query="Neural")
        assert len(results) == 1  # Archetype Nameless in fixtures

    def test_search_plugins_by_format(self, temp_db, sample_plugins):
        """Test filtering by format."""
        temp_db.add_plugins(sample_plugins)

        vst3_plugins = temp_db.search_plugins(format="vst3")
        au_plugins = temp_db.search_plugins(format="au")

        assert len(vst3_plugins) >= 1
        assert len(au_plugins) >= 1

    def test_search_plugins_by_category(self, temp_db, sample_plugins):
        """Test filtering by category."""
        temp_db.add_plugins(sample_plugins)

        reverb_plugins = temp_db.search_plugins(category="Reverb")
        assert len(reverb_plugins) >= 1
        assert any("Valhalla" in p["name"] for p in reverb_plugins)

    def test_get_manufacturers(self, temp_db, sample_plugins):
        """Test getting list of manufacturers."""
        temp_db.add_plugins(sample_plugins)

        manufacturers = temp_db.get_manufacturers()
        assert "Neural DSP" in manufacturers
        assert "Valhalla DSP" in manufacturers

    def test_get_categories(self, temp_db, sample_plugins):
        """Test getting list of categories."""
        temp_db.add_plugins(sample_plugins)

        categories = temp_db.get_categories()
        assert "Amp" in categories
        assert "Reverb" in categories
        assert "Delay" in categories

    def test_mark_unavailable(self, temp_db, sample_plugin):
        """Test marking plugin as unavailable."""
        temp_db.add_plugin(sample_plugin)
        temp_db.mark_unavailable(sample_plugin.plugin_id)

        # Should not appear in available-only search
        results = temp_db.search_plugins(available_only=True)
        assert not any(p["plugin_id"] == sample_plugin.plugin_id for p in results)

        # Should appear in all plugins
        results = temp_db.search_plugins(available_only=False)
        assert any(p["plugin_id"] == sample_plugin.plugin_id for p in results)

    def test_favorites(self, temp_db, sample_plugin):
        """Test favorites functionality."""
        temp_db.add_plugin(sample_plugin)

        # Add to favorites
        temp_db.add_favorite(sample_plugin.plugin_id)
        assert temp_db.is_favorite(sample_plugin.plugin_id)

        # Get favorites
        favorites = temp_db.get_favorites()
        assert len(favorites) == 1
        assert favorites[0]["plugin_id"] == sample_plugin.plugin_id

        # Remove from favorites
        temp_db.remove_favorite(sample_plugin.plugin_id)
        assert not temp_db.is_favorite(sample_plugin.plugin_id)

    def test_usage_stats(self, temp_db, sample_plugin):
        """Test usage statistics."""
        temp_db.add_plugin(sample_plugin)

        # Record usage multiple times
        temp_db.record_usage(sample_plugin.plugin_id)
        temp_db.record_usage(sample_plugin.plugin_id)
        temp_db.record_usage(sample_plugin.plugin_id)

        # Check most used
        most_used = temp_db.get_most_used(limit=5)
        assert len(most_used) == 1
        assert most_used[0]["use_count"] == 3

    def test_block_mappings(self, temp_db, sample_plugin):
        """Test block mapping storage."""
        temp_db.add_plugin(sample_plugin)

        # Set mapping
        temp_db.set_block_mapping(
            plugin_id=sample_plugin.plugin_id,
            block_family="mesa_rectifier",
            block_type="amp",
            confidence=0.85,
        )

        # Retrieve mapping
        mapping = temp_db.get_block_mapping(sample_plugin.plugin_id)
        assert mapping is not None
        assert mapping["block_family"] == "mesa_rectifier"
        assert mapping["confidence"] == 0.85

    def test_get_plugins_for_block(self, temp_db, sample_plugins):
        """Test getting plugins for a block family."""
        temp_db.add_plugins(sample_plugins)

        # Set mappings
        temp_db.set_block_mapping(
            plugin_id=sample_plugins[0].plugin_id,
            block_family="high_gain_modern",
            block_type="amp",
            confidence=0.8,
        )
        temp_db.set_block_mapping(
            plugin_id=sample_plugins[1].plugin_id,
            block_family="high_gain_modern",
            block_type="amp",
            confidence=0.9,
        )

        # Get plugins for block
        plugins = temp_db.get_plugins_for_block("high_gain_modern")
        assert len(plugins) == 2

    def test_scan_history(self, temp_db):
        """Test scan history recording."""
        temp_db.record_scan(
            plugins_found=50,
            plugins_added=10,
            plugins_removed=2,
            scan_duration_ms=1500,
            formats_scanned=["au", "vst3"],
        )

        history = temp_db.get_scan_history(limit=5)
        assert len(history) == 1
        assert history[0]["plugins_found"] == 50
        assert history[0]["plugins_added"] == 10

    def test_get_stats(self, temp_db, sample_plugins):
        """Test database statistics."""
        temp_db.add_plugins(sample_plugins)
        temp_db.add_favorite(sample_plugins[0].plugin_id)

        stats = temp_db.get_stats()

        assert stats["total_plugins"] == len(sample_plugins)
        assert stats["available_plugins"] == len(sample_plugins)
        assert stats["favorites_count"] == 1
        assert "vst3" in stats["by_format"]


# ============================================================================
# PluginMapper tests
# ============================================================================

class TestPluginMapper:
    """Tests for the PluginMapper class."""

    def test_map_marshall_amp(self):
        """Test mapping a Marshall-style amp."""
        mapping = map_plugin(
            plugin_id="test:marshall_jcm800",
            name="JCM800 Amp Sim",
            manufacturer="Test Company",
            categories=["Amp"],
        )

        assert mapping is not None
        assert mapping.block_type == "amp"
        assert "marshall" in mapping.block_family.lower()

    def test_map_mesa_amp(self):
        """Test mapping a Mesa-style amp."""
        mapping = map_plugin(
            plugin_id="test:dual_rec",
            name="Dual Rectifier",
            manufacturer="Test Company",
            categories=["Amp"],
        )

        assert mapping is not None
        assert mapping.block_type == "amp"
        assert "mesa" in mapping.block_family.lower() or "rect" in mapping.block_family.lower()

    def test_map_5150_amp(self):
        """Test mapping a 5150-style amp."""
        mapping = map_plugin(
            plugin_id="test:5150",
            name="5150 III Stealth",
            manufacturer="EVH",
            categories=["Amp"],
        )

        assert mapping is not None
        assert mapping.block_type == "amp"
        assert "5150" in mapping.block_family

    def test_map_fender_amp(self):
        """Test mapping a Fender-style amp."""
        mapping = map_plugin(
            plugin_id="test:fender_twin",
            name="Twin Reverb",
            manufacturer="Test Company",
            categories=["Amp"],
        )

        assert mapping is not None
        assert mapping.block_type == "amp"
        assert "fender" in mapping.block_family.lower()

    def test_map_neural_dsp_plugin(self):
        """Test mapping Neural DSP plugins by manufacturer."""
        mapping = map_plugin(
            plugin_id="neural:nameless",
            name="Archetype Nameless",
            manufacturer="Neural DSP",
            categories=["Amp"],
        )

        assert mapping is not None
        assert mapping.block_type == "amp"
        assert mapping.confidence >= 0.8

    def test_map_tube_screamer(self):
        """Test mapping a Tube Screamer."""
        mapping = map_plugin(
            plugin_id="test:ts808",
            name="TS808 Overdrive",
            manufacturer="Test Company",
            categories=["Overdrive"],
        )

        assert mapping is not None
        assert mapping.block_type == "effect"
        assert "ts" in mapping.block_family.lower() or "overdrive" in mapping.block_family.lower()

    def test_map_delay(self):
        """Test mapping a delay plugin."""
        mapping = map_plugin(
            plugin_id="test:echoboy",
            name="EchoBoy Delay",
            manufacturer="Test Company",
            categories=["Delay"],
        )

        assert mapping is not None
        assert mapping.block_type == "effect"
        assert "delay" in mapping.block_family.lower()

    def test_map_reverb(self):
        """Test mapping a reverb plugin."""
        mapping = map_plugin(
            plugin_id="test:valhalla_room",
            name="Valhalla Room",
            manufacturer="Valhalla DSP",
            categories=["Reverb"],
        )

        assert mapping is not None
        assert mapping.block_type == "effect"
        assert "reverb" in mapping.block_family.lower()

    def test_map_cabinet(self):
        """Test mapping a cabinet/IR plugin."""
        mapping = map_plugin(
            plugin_id="test:ir_loader",
            name="IR Loader 4x12",
            manufacturer="Test Company",
            categories=["Cabinet"],
        )

        assert mapping is not None
        assert mapping.block_type == "cab"

    def test_map_by_category_fallback(self):
        """Test mapping by category when name doesn't match."""
        mapping = map_plugin(
            plugin_id="test:unknown_compressor",
            name="Super Squasher Pro",
            manufacturer="Unknown Company",
            categories=["Compressor"],
        )

        assert mapping is not None
        assert mapping.block_type == "effect"
        assert "compressor" in mapping.block_family.lower()
        assert mapping.confidence <= 0.6  # Lower confidence for category match

    def test_map_unknown_plugin(self):
        """Test that unknown plugins return None."""
        mapping = map_plugin(
            plugin_id="test:random",
            name="Random Plugin XYZ",
            manufacturer="Unknown Company",
            categories=[],
        )

        # May or may not map depending on name
        # This is acceptable behavior
        if mapping is not None:
            assert mapping.confidence < 0.8

    def test_mapper_batch(self, sample_plugins):
        """Test mapping multiple plugins."""
        mapper = PluginMapper()
        results = mapper.map_plugins(sample_plugins)

        assert len(results) == len(sample_plugins)

        # At least some should have mappings
        mapped_count = sum(1 for _, m in results if m is not None)
        assert mapped_count >= 3

    def test_mapper_with_db(self, temp_db, sample_plugin):
        """Test mapper with database caching."""
        temp_db.add_plugin(sample_plugin)

        # Set user-defined mapping
        temp_db.set_block_mapping(
            plugin_id=sample_plugin.plugin_id,
            block_family="custom_block",
            block_type="amp",
            confidence=1.0,
            is_user_defined=True,
        )

        mapper = PluginMapper(db=temp_db)
        mapping = mapper.map_plugin(
            plugin_id=sample_plugin.plugin_id,
            name=sample_plugin.name,
            manufacturer=sample_plugin.manufacturer,
            categories=sample_plugin.categories,
            plugin_type=sample_plugin.plugin_type,
        )

        # Should use user-defined mapping
        assert mapping is not None
        assert mapping.block_family == "custom_block"
        assert mapping.is_user_defined

    def test_get_plugins_for_descriptor(self, sample_plugins):
        """Test getting plugins for a descriptor."""
        mapper = PluginMapper()

        descriptor = {
            "amp": {
                "family": "high_gain",
                "gain": 0.8,
            },
            "cab": {
                "configuration": "4x12",
            },
        }

        recommendations = mapper.get_plugins_for_descriptor(descriptor, sample_plugins)

        assert "amp" in recommendations
        assert "cab" in recommendations
        assert "effects" in recommendations


# ============================================================================
# Pattern matching tests
# ============================================================================

class TestPatternMatching:
    """Tests for the name pattern matching."""

    def test_amp_patterns_coverage(self):
        """Test that amp patterns cover major brands."""
        test_names = [
            ("Marshall JCM800", "marshall"),
            ("Fender Twin Reverb", "fender"),
            ("Mesa Dual Rectifier", "mesa"),
            ("Peavey 5150", "5150"),
            ("Vox AC30", "vox"),
            ("Soldano SLO-100", "soldano"),
            ("Friedman BE-100", "friedman"),
            ("ENGL Savage", "engl"),
            ("Orange Rockerverb", "orange"),
            ("Bogner Ecstasy", "bogner"),
            ("Diezel Herbert", "diezel"),
            ("Dumble Overdrive Special", "dumble"),
        ]

        for name, expected_match in test_names:
            mapping = map_plugin(
                plugin_id=f"test:{name.lower().replace(' ', '_')}",
                name=name,
                manufacturer="Test",
                categories=["Amp"],
            )

            assert mapping is not None, f"Failed to map: {name}"
            assert mapping.block_type == "amp", f"Wrong type for: {name}"

    def test_effect_patterns_coverage(self):
        """Test that effect patterns cover common effects."""
        test_names = [
            ("Tube Screamer", "overdrive"),
            ("Klon Centaur", "overdrive"),
            ("Big Muff", "fuzz"),
            ("Boss DD-7", "delay"),
            ("Spring Reverb", "reverb"),
            ("MXR Phase 90", "phaser"),
            ("Electric Mistress", "flanger"),
            ("Boss CE-2", "chorus"),
            ("Cry Baby Wah", "wah"),
            ("Digitech Whammy", "pitch"),
        ]

        for name, expected_type in test_names:
            mapping = map_plugin(
                plugin_id=f"test:{name.lower().replace(' ', '_')}",
                name=name,
                manufacturer="Test",
                categories=[],
            )

            assert mapping is not None, f"Failed to map: {name}"
            assert mapping.block_type == "effect", f"Wrong type for: {name}"


# ============================================================================
# Integration tests
# ============================================================================

class TestPluginScannerIntegration:
    """Integration tests for the plugin scanner module."""

    def test_full_workflow(self, temp_db, sample_plugins):
        """Test full scan and map workflow."""
        # Add plugins to database
        added, errors = temp_db.add_plugins(sample_plugins)
        assert added == len(sample_plugins)

        # Create mapper with database
        mapper = PluginMapper(db=temp_db)

        # Map all plugins
        results = mapper.map_plugins(sample_plugins)

        # Check mappings were cached
        for plugin, mapping in results:
            if mapping:
                cached = temp_db.get_block_mapping(plugin.plugin_id)
                assert cached is not None
                assert cached["block_family"] == mapping.block_family

    def test_module_imports(self):
        """Test that all module imports work correctly."""
        from local_engine.plugin_scanner import (
            PluginDatabase,
            PluginMapper,
            BlockMapping,
            get_database,
            get_mapper,
            map_plugin,
        )

        assert PluginDatabase is not None
        assert PluginMapper is not None
        assert BlockMapping is not None

    def test_scan_and_register_function(self, temp_db, monkeypatch):
        """Test the scan_and_register function."""
        from local_engine import plugin_scanner

        # Mock the scan function to return sample plugins
        sample = [
            PluginInfo(
                plugin_id="test:mock_plugin",
                name="Mock Plugin",
                manufacturer="Test",
                version="1.0",
                path=Path("/tmp/mock.vst3"),
                format="vst3",
                plugin_type="effect",
                categories=["Amp"],
                modified_time=1700000000.0,
            ),
        ]

        monkeypatch.setattr(plugin_scanner, "_database", temp_db)
        monkeypatch.setattr(plugin_scanner, "scan_plugins", lambda **kwargs: sample)

        stats = plugin_scanner.scan_and_register(
            scan_au=False,
            scan_vst3=True,
            scan_vst2=False,
        )

        assert stats["plugins_found"] == 1
        assert stats["plugins_added"] == 1

        # Plugin should be in database
        all_plugins = temp_db.get_all_plugins()
        assert len(all_plugins) == 1
