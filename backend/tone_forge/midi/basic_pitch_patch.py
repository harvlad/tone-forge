"""Patch basic-pitch for better performance on macOS.

Fixes two issues in basic-pitch:
1. Debug print statements in CoreML inference that slow down processing
2. CoreML model loaded with CPU_ONLY instead of using GPU/Apple Neural Engine

Import this BEFORE importing basic_pitch.
"""
import sys
import logging
import platform

logger = logging.getLogger(__name__)

# Track if we've already patched
_patched = False


def apply_patch():
    """Apply patches to fix basic-pitch performance issues."""
    global _patched
    if _patched:
        return

    is_apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"

    if not is_apple_silicon:
        logger.info("Not Apple Silicon, skipping CoreML GPU patch")
        return

    try:
        import coremltools as ct

        # Patch the MLModel class to use ALL compute units by default
        _original_mlmodel_init = ct.models.MLModel.__init__

        def patched_mlmodel_init(self, *args, **kwargs):
            # Force GPU/ANE usage unless explicitly set to CPU_ONLY
            if 'compute_units' not in kwargs:
                kwargs['compute_units'] = ct.ComputeUnit.ALL
            elif kwargs.get('compute_units') == ct.ComputeUnit.CPU_ONLY:
                # Override CPU_ONLY to use ALL (GPU + ANE + CPU)
                kwargs['compute_units'] = ct.ComputeUnit.ALL
                logger.info("Overriding CoreML CPU_ONLY to use GPU/ANE acceleration")
            _original_mlmodel_init(self, *args, **kwargs)

        ct.models.MLModel.__init__ = patched_mlmodel_init
        _patched = True
        logger.info("Applied CoreML patch for GPU/ANE acceleration on Apple Silicon")

    except ImportError:
        logger.debug("coremltools not installed, skipping patch")
    except Exception as e:
        logger.warning(f"Could not patch CoreML: {e}")


# Auto-apply when imported
apply_patch()
