"""Block ranker using LightGBM for ML-based recommendations.

Replaces rule-based first-match block selection with ML-ranked
recommendations. Uses LightGBM trained on:
- Heuristic labels (initial bootstrap)
- User feedback (accepted/rejected recommendations)
- Export edits (what users changed)

Falls back to heuristic scoring when ML models aren't available.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import numpy as np

from .feature_builder import (
    RankingFeatures,
    build_ranking_features,
    build_features_batch,
)

logger = logging.getLogger(__name__)

# Model paths
DEFAULT_MODEL_DIR = Path.home() / ".toneforge" / "models" / "ranker"


@dataclass
class ScoredBlock:
    """A block with its ranking score and explanation."""

    block_id: str
    block: Dict
    score: float
    rank: int
    features: RankingFeatures
    explanation: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        """Generate explanation from features if not provided."""
        if not self.explanation:
            self.explanation = self._generate_explanation()

    def _generate_explanation(self) -> Dict[str, float]:
        """Generate human-readable explanation of why this block was ranked."""
        exp = {}

        # Top contributing features
        if self.features.family_exact_match > 0.5:
            exp["family_match"] = self.features.family_exact_match
        if self.features.family_related_match > 0.5:
            exp["related_family"] = self.features.family_related_match
        if self.features.gain_in_range > 0.5:
            exp["gain_compatible"] = self.features.gain_in_range
        if self.features.user_used_before > 0.5:
            exp["previously_used"] = self.features.user_used_before
        if self.features.block_popularity > 0.7:
            exp["popular_choice"] = self.features.block_popularity
        if self.features.voicing_match_bass > 0.7:
            exp["bass_match"] = self.features.voicing_match_bass
        if self.features.voicing_match_mid > 0.7:
            exp["mid_match"] = self.features.voicing_match_mid
        if self.features.voicing_match_treble > 0.7:
            exp["treble_match"] = self.features.voicing_match_treble
        if self.features.style_match > 0.5:
            exp["style_match"] = self.features.style_match
        if self.features.speaker_char_match > 0.5:
            exp["speaker_match"] = self.features.speaker_char_match

        return exp


class BlockRanker:
    """ML-based block ranker using LightGBM.

    Falls back to heuristic scoring when models aren't available.
    Supports training from user feedback for personalization.
    """

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        use_ml: bool = True,
    ):
        """Initialize the block ranker.

        Args:
            model_dir: Directory containing trained models
            use_ml: Whether to use ML models (vs pure heuristics)
        """
        self.model_dir = model_dir or DEFAULT_MODEL_DIR
        self.use_ml = use_ml
        self._model = None
        self._model_loaded = False

        # Feature weights for heuristic fallback
        self._heuristic_weights = self._get_heuristic_weights()

        if use_ml:
            self._try_load_model()

    def _get_heuristic_weights(self) -> np.ndarray:
        """Get feature weights for heuristic scoring.

        These weights approximate what a trained model would learn,
        based on domain knowledge about what makes a good block match.
        """
        return np.array([
            # Compatibility features (most important)
            3.0,   # family_exact_match - critical
            1.5,   # family_related_match - helpful
            2.0,   # gain_in_range - very important
            -1.0,  # gain_distance - penalty for distance
            0.8,   # voicing_match_bass
            1.0,   # voicing_match_mid - mids are important
            0.8,   # voicing_match_treble
            1.5,   # style_match - for effects
            1.2,   # configuration_match - cab config
            1.2,   # speaker_char_match - speaker type

            # Block characteristics
            0.5,   # block_popularity
            0.7,   # block_avg_rating
            -0.3,  # block_edit_rate - high edit rate = users change it
            0.4,   # block_category_affinity
            0.6,   # block_platform_native
            -0.2,  # block_price_tier - slight preference for accessible
            0.3,   # block_versatility
            -1.0,  # block_is_fallback - penalty for fallback blocks

            # User preferences
            1.5,   # user_used_before - strong signal
            0.8,   # user_family_preference
            0.4,   # user_gain_bias
            0.3,   # user_effects_affinity
            0.2,   # user_session_context

            # Confidence
            0.5,   # descriptor_confidence
            0.3,   # analysis_quality
        ], dtype=np.float32)

    def _try_load_model(self) -> bool:
        """Try to load the LightGBM model."""
        model_path = self.model_dir / "ranker.lgb"

        if not model_path.exists():
            logger.debug("No ranker model found at %s", model_path)
            return False

        try:
            import lightgbm as lgb
            self._model = lgb.Booster(model_file=str(model_path))
            self._model_loaded = True
            logger.info("Loaded ranker model from %s", model_path)
            return True
        except ImportError:
            logger.debug("LightGBM not available, using heuristic ranking")
            return False
        except Exception as e:
            logger.warning("Failed to load ranker model: %s", e)
            return False

    def is_ml_ready(self) -> bool:
        """Check if ML model is loaded and ready."""
        return self._model_loaded and self._model is not None

    def rank_blocks(
        self,
        descriptor: Dict,
        blocks: List[Dict],
        slot: str,
        user_prefs: Optional[Dict] = None,
        block_stats: Optional[Dict[str, Dict]] = None,
        top_k: Optional[int] = None,
    ) -> List[ScoredBlock]:
        """Rank candidate blocks for a slot.

        Args:
            descriptor: ToneDescriptor as dict
            blocks: List of candidate blocks to rank
            slot: Slot type ("amp", "cab", "drive", etc.)
            user_prefs: Optional user preferences
            block_stats: Optional dict mapping block_id -> usage stats
            top_k: Return only top k results (None = all)

        Returns:
            List of ScoredBlock sorted by score (highest first)
        """
        if not blocks:
            return []

        # Build feature matrix
        feature_matrix, block_ids = build_features_batch(
            descriptor=descriptor,
            blocks=blocks,
            slot=slot,
            user_prefs=user_prefs,
            block_stats=block_stats,
        )

        # Score using ML or heuristics
        if self.is_ml_ready():
            scores = self._score_ml(feature_matrix)
        else:
            scores = self._score_heuristic(feature_matrix)

        # Build scored blocks
        scored_blocks = []
        block_map = {b.get("id", ""): b for b in blocks}

        for i, (block_id, score) in enumerate(zip(block_ids, scores)):
            features = build_ranking_features(
                descriptor=descriptor,
                block=block_map.get(block_id, {}),
                slot=slot,
                user_prefs=user_prefs,
                block_stats=block_stats.get(block_id) if block_stats else None,
            )

            scored_blocks.append(ScoredBlock(
                block_id=block_id,
                block=block_map.get(block_id, {}),
                score=float(score),
                rank=0,  # Will be set after sorting
                features=features,
            ))

        # Sort by score (highest first)
        scored_blocks.sort(key=lambda x: x.score, reverse=True)

        # Assign ranks
        for i, block in enumerate(scored_blocks):
            block.rank = i + 1

        # Return top k if specified
        if top_k is not None:
            scored_blocks = scored_blocks[:top_k]

        return scored_blocks

    def _score_ml(self, feature_matrix: np.ndarray) -> np.ndarray:
        """Score using LightGBM model."""
        return self._model.predict(feature_matrix)

    def _score_heuristic(self, feature_matrix: np.ndarray) -> np.ndarray:
        """Score using heuristic weights.

        This provides reasonable ranking when ML models aren't available.
        """
        # Simple weighted sum
        scores = np.dot(feature_matrix, self._heuristic_weights)

        # Normalize to 0-1 range
        if len(scores) > 1:
            min_score = scores.min()
            max_score = scores.max()
            if max_score > min_score:
                scores = (scores - min_score) / (max_score - min_score)
            else:
                scores = np.ones_like(scores) * 0.5
        elif len(scores) == 1:
            scores = np.array([0.5])

        return scores

    def get_top_block(
        self,
        descriptor: Dict,
        blocks: List[Dict],
        slot: str,
        user_prefs: Optional[Dict] = None,
        block_stats: Optional[Dict[str, Dict]] = None,
    ) -> Optional[ScoredBlock]:
        """Get the top-ranked block for a slot.

        Convenience method for getting the best recommendation.
        """
        ranked = self.rank_blocks(
            descriptor=descriptor,
            blocks=blocks,
            slot=slot,
            user_prefs=user_prefs,
            block_stats=block_stats,
            top_k=1,
        )
        return ranked[0] if ranked else None

    def explain_ranking(
        self,
        scored_block: ScoredBlock,
        verbose: bool = False,
    ) -> str:
        """Generate a human-readable explanation of why a block was ranked.

        Args:
            scored_block: The scored block to explain
            verbose: Include all feature values

        Returns:
            Human-readable explanation string
        """
        lines = [f"Block: {scored_block.block_id}"]
        lines.append(f"Score: {scored_block.score:.3f} (Rank #{scored_block.rank})")

        if scored_block.explanation:
            lines.append("Key factors:")
            for factor, value in sorted(
                scored_block.explanation.items(),
                key=lambda x: x[1],
                reverse=True
            ):
                lines.append(f"  - {factor}: {value:.2f}")

        if verbose:
            lines.append("\nAll features:")
            for name, value in zip(
                RankingFeatures.feature_names(),
                scored_block.features.to_array()
            ):
                lines.append(f"  {name}: {value:.3f}")

        return "\n".join(lines)


# Module-level singleton for convenience
_ranker: Optional[BlockRanker] = None


def get_ranker(
    model_dir: Optional[Path] = None,
    use_ml: bool = True,
) -> BlockRanker:
    """Get or create the global BlockRanker instance."""
    global _ranker

    if _ranker is None:
        _ranker = BlockRanker(model_dir=model_dir, use_ml=use_ml)

    return _ranker


def rank_blocks(
    descriptor: Dict,
    blocks: List[Dict],
    slot: str,
    user_prefs: Optional[Dict] = None,
    block_stats: Optional[Dict[str, Dict]] = None,
    top_k: Optional[int] = None,
) -> List[ScoredBlock]:
    """Rank blocks using the global ranker instance.

    Convenience function for one-off ranking.
    """
    return get_ranker().rank_blocks(
        descriptor=descriptor,
        blocks=blocks,
        slot=slot,
        user_prefs=user_prefs,
        block_stats=block_stats,
        top_k=top_k,
    )


def is_ranker_ready() -> bool:
    """Check if the global ranker has ML models loaded."""
    return get_ranker().is_ml_ready()
