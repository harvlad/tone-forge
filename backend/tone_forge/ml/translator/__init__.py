"""ML-based translator intelligence.

Replaces rule-based first-match block selection with ML-ranked
recommendations. Uses LightGBM ranking model trained on:
- Heuristic labels (initial bootstrap)
- User feedback (accepted/rejected recommendations)
- Export edits (what users changed)

Falls back to rule-based selection when models aren't available.
"""
from __future__ import annotations

from .feature_builder import (
    RankingFeatures,
    build_ranking_features,
    build_features_batch,
)
from .ranker import (
    BlockRanker,
    ScoredBlock,
    get_ranker,
    rank_blocks,
    is_ranker_ready,
)
from .feedback import (
    FeedbackCollector,
    FeedbackEvent,
    get_collector,
    record_selection,
    record_edit,
    record_export,
)

__all__ = [
    # Feature building
    "RankingFeatures",
    "build_ranking_features",
    "build_features_batch",
    # Ranking
    "BlockRanker",
    "ScoredBlock",
    "get_ranker",
    "rank_blocks",
    "is_ranker_ready",
    # Feedback
    "FeedbackCollector",
    "FeedbackEvent",
    "get_collector",
    "record_selection",
    "record_edit",
    "record_export",
    "submit_feedback",
    "get_feedback_stats",
]


def submit_feedback(
    analysis_id: str,
    slot: str,
    selected_block_id: str,
    selected_block_family: str,
    was_top_pick: bool = False,
    original_rank: int = 0,
    rating: float = None,
    notes: str = None,
) -> str:
    """Submit user feedback on a block recommendation.

    Args:
        analysis_id: Analysis/descriptor ID
        slot: Slot type (amp, cab, etc.)
        selected_block_id: ID of the selected block
        selected_block_family: Family of the selected block
        was_top_pick: Whether this was the top recommendation
        original_rank: Rank in the original recommendation list
        rating: Optional user rating (1-5)
        notes: Optional notes

    Returns:
        Event ID
    """
    collector = get_collector()

    # Determine if this is a selection or rejection
    if was_top_pick or original_rank <= 1:
        return collector.record_selection(
            descriptor_hash=analysis_id,
            slot=slot,
            block_id=selected_block_id,
            was_top_recommendation=was_top_pick,
            recommendation_rank=original_rank,
        )
    else:
        # User picked something other than top - record as selection with lower rank
        return collector.record_selection(
            descriptor_hash=analysis_id,
            slot=slot,
            block_id=selected_block_id,
            was_top_recommendation=False,
            recommendation_rank=original_rank,
        )


def get_feedback_stats() -> dict:
    """Get feedback collection statistics.

    Returns:
        Dictionary with feedback stats
    """
    collector = get_collector()
    return collector.get_stats()
