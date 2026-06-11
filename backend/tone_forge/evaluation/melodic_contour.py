"""Melodic contour extraction and comparison.

Melodic contour represents the shape of a melody in terms of
pitch direction (up/down/same) rather than absolute pitches.
This allows comparison of melodies independent of key.

Used for perceptual similarity scoring to assess whether
extracted MIDI preserves the "shape" of the original melody.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple, Optional

import numpy as np

logger = logging.getLogger(__name__)


class ContourType(str, Enum):
    """Direction of melodic movement."""
    UP = "up"
    DOWN = "down"
    SAME = "same"


@dataclass
class ContourSegment:
    """A segment of melodic contour."""
    direction: ContourType
    interval: int  # Semitones (can be 0 for SAME)
    start_idx: int
    end_idx: int

    @property
    def length(self) -> int:
        return self.end_idx - self.start_idx


def extract_contour(
    pitches: List[int],
    same_threshold: int = 0,
) -> List[ContourType]:
    """Extract melodic contour from pitch sequence.

    Args:
        pitches: List of MIDI pitch values
        same_threshold: Interval threshold for "same" (default 0 = exact match only)

    Returns:
        List of ContourType directions
    """
    if len(pitches) < 2:
        return []

    contour = []
    for i in range(1, len(pitches)):
        interval = pitches[i] - pitches[i-1]

        if abs(interval) <= same_threshold:
            contour.append(ContourType.SAME)
        elif interval > 0:
            contour.append(ContourType.UP)
        else:
            contour.append(ContourType.DOWN)

    return contour


def extract_contour_segments(
    pitches: List[int],
) -> List[ContourSegment]:
    """Extract contour as segments of sustained direction.

    Args:
        pitches: List of MIDI pitch values

    Returns:
        List of ContourSegment objects
    """
    if len(pitches) < 2:
        return []

    segments = []
    current_direction = None
    current_start = 0
    total_interval = 0

    for i in range(1, len(pitches)):
        interval = pitches[i] - pitches[i-1]

        if interval > 0:
            direction = ContourType.UP
        elif interval < 0:
            direction = ContourType.DOWN
        else:
            direction = ContourType.SAME

        if current_direction is None:
            current_direction = direction
            total_interval = interval
        elif direction == current_direction or direction == ContourType.SAME:
            total_interval += interval
        else:
            # Direction changed - save segment
            segments.append(ContourSegment(
                direction=current_direction,
                interval=total_interval,
                start_idx=current_start,
                end_idx=i,
            ))
            current_direction = direction
            current_start = i
            total_interval = interval

    # Don't forget last segment
    if current_direction is not None:
        segments.append(ContourSegment(
            direction=current_direction,
            interval=total_interval,
            start_idx=current_start,
            end_idx=len(pitches),
        ))

    return segments


def compare_contours(
    contour_a: List[ContourType],
    contour_b: List[ContourType],
    method: str = "lcs",
) -> float:
    """Compare two melodic contours.

    Args:
        contour_a: First contour
        contour_b: Second contour
        method: Comparison method
            - "lcs": Longest common subsequence ratio
            - "edit": Edit distance ratio
            - "direct": Direct match ratio (same length required)

    Returns:
        Similarity score 0-1 (higher = more similar)
    """
    if len(contour_a) == 0 and len(contour_b) == 0:
        return 1.0
    if len(contour_a) == 0 or len(contour_b) == 0:
        return 0.0

    if method == "lcs":
        return _lcs_similarity(contour_a, contour_b)
    elif method == "edit":
        return _edit_distance_similarity(contour_a, contour_b)
    elif method == "direct":
        return _direct_similarity(contour_a, contour_b)
    else:
        return _lcs_similarity(contour_a, contour_b)


def _lcs_similarity(a: List[ContourType], b: List[ContourType]) -> float:
    """Calculate LCS-based similarity."""
    m, n = len(a), len(b)

    # Build LCS table
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])

    lcs_length = dp[m][n]

    # Normalize by average length
    return 2 * lcs_length / (m + n)


def _edit_distance_similarity(a: List[ContourType], b: List[ContourType]) -> float:
    """Calculate edit distance-based similarity."""
    m, n = len(a), len(b)

    # Build edit distance table
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(
                    dp[i-1][j],    # Delete
                    dp[i][j-1],    # Insert
                    dp[i-1][j-1],  # Replace
                )

    edit_distance = dp[m][n]
    max_length = max(m, n)

    if max_length == 0:
        return 1.0

    return 1.0 - (edit_distance / max_length)


def _direct_similarity(a: List[ContourType], b: List[ContourType]) -> float:
    """Calculate direct match ratio (handles different lengths)."""
    # Resample longer to shorter
    if len(a) != len(b):
        if len(a) > len(b):
            a = _resample_contour(a, len(b))
        else:
            b = _resample_contour(b, len(a))

    if len(a) == 0:
        return 1.0

    matches = sum(1 for i in range(len(a)) if a[i] == b[i])
    return matches / len(a)


def _resample_contour(
    contour: List[ContourType],
    target_length: int,
) -> List[ContourType]:
    """Resample contour to target length."""
    if target_length == 0:
        return []

    if len(contour) == 0:
        return [ContourType.SAME] * target_length

    # Use linear interpolation via indices
    indices = np.linspace(0, len(contour) - 1, target_length)
    return [contour[int(round(idx))] for idx in indices]


def contour_to_string(contour: List[ContourType]) -> str:
    """Convert contour to string representation.

    Args:
        contour: List of ContourType

    Returns:
        String like "U D S U U D" or "↑↓-↑↑↓"
    """
    symbols = {
        ContourType.UP: "↑",
        ContourType.DOWN: "↓",
        ContourType.SAME: "-",
    }
    return "".join(symbols[c] for c in contour)


def string_to_contour(s: str) -> List[ContourType]:
    """Convert string representation to contour.

    Args:
        s: String like "UDS" or "↑↓-"

    Returns:
        List of ContourType
    """
    mapping = {
        "U": ContourType.UP,
        "↑": ContourType.UP,
        "+": ContourType.UP,
        "D": ContourType.DOWN,
        "↓": ContourType.DOWN,
        "-": ContourType.SAME,
        "S": ContourType.SAME,
        "=": ContourType.SAME,
    }

    return [mapping.get(c.upper(), ContourType.SAME) for c in s if c.upper() in mapping]


def extract_parsons_code(pitches: List[int]) -> str:
    """Extract Parsons code from pitch sequence.

    Parsons code is a simplified melodic contour:
    - '*' for first note
    - 'U' for up
    - 'D' for down
    - 'R' for repeat (same)

    Used for melody search and comparison.
    """
    if not pitches:
        return ""

    code = "*"  # First note marker

    for i in range(1, len(pitches)):
        interval = pitches[i] - pitches[i-1]
        if interval > 0:
            code += "U"
        elif interval < 0:
            code += "D"
        else:
            code += "R"

    return code


def compare_parsons_codes(code_a: str, code_b: str) -> float:
    """Compare two Parsons codes.

    Args:
        code_a: First Parsons code
        code_b: Second Parsons code

    Returns:
        Similarity 0-1
    """
    # Strip first '*' markers
    a = code_a.lstrip("*")
    b = code_b.lstrip("*")

    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    # Convert to contour and compare
    contour_a = string_to_contour(a.replace("R", "S"))
    contour_b = string_to_contour(b.replace("R", "S"))

    return compare_contours(contour_a, contour_b)


def find_contour_motifs(
    pitches: List[int],
    min_length: int = 3,
    max_length: int = 8,
) -> List[Tuple[str, int, int]]:
    """Find repeating contour motifs in melody.

    Args:
        pitches: List of MIDI pitches
        min_length: Minimum motif length
        max_length: Maximum motif length

    Returns:
        List of (contour_string, start_idx, count) tuples
    """
    if len(pitches) < min_length:
        return []

    contour = extract_contour(pitches)
    motifs = {}

    # Slide window and collect patterns
    for length in range(min_length, min(max_length + 1, len(contour) + 1)):
        for i in range(len(contour) - length + 1):
            pattern = tuple(contour[i:i+length])
            pattern_str = contour_to_string(list(pattern))

            if pattern_str not in motifs:
                motifs[pattern_str] = (i, 1)
            else:
                motifs[pattern_str] = (motifs[pattern_str][0], motifs[pattern_str][1] + 1)

    # Filter to repeated motifs
    repeated = [
        (pattern, start, count)
        for pattern, (start, count) in motifs.items()
        if count > 1
    ]

    # Sort by count (most common first)
    repeated.sort(key=lambda x: -x[2])

    return repeated


def contour_complexity(contour: List[ContourType]) -> float:
    """Calculate melodic complexity from contour.

    Higher values = more complex melody (more direction changes).

    Args:
        contour: Melodic contour

    Returns:
        Complexity score 0-1
    """
    if len(contour) < 2:
        return 0.0

    changes = 0
    for i in range(1, len(contour)):
        if contour[i] != contour[i-1]:
            changes += 1

    # Normalize by length - 1
    return changes / (len(contour) - 1)


def contour_balance(contour: List[ContourType]) -> float:
    """Calculate balance between up and down movement.

    Values near 0.5 indicate balanced melody.
    Values near 0 = mostly descending.
    Values near 1 = mostly ascending.

    Args:
        contour: Melodic contour

    Returns:
        Balance score 0-1
    """
    if not contour:
        return 0.5

    up_count = sum(1 for c in contour if c == ContourType.UP)
    down_count = sum(1 for c in contour if c == ContourType.DOWN)
    total = up_count + down_count

    if total == 0:
        return 0.5

    return up_count / total
