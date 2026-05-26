"""Patch basic-pitch for better performance on macOS.

Fixes two issues in basic-pitch:
1. Debug print statements in CoreML inference that slow down processing
2. CoreML model loaded with CPU_ONLY instead of using GPU/Apple Neural Engine

Import this BEFORE importing basic_pitch.
"""
import sys
import logging

logger = logging.getLogger(__name__)

# Track if we've already patched
_patched = False


def apply_patch():
    """Apply patches to fix basic-pitch performance issues."""
    global _patched
    if _patched:
        return

    try:
        import basic_pitch.inference as inference_module
        import coremltools as ct

        # Store original _load_model method
        original_load_model = inference_module.Model._load_model

        def patched_load_model(self, model_path):
            """Patched model loader that uses GPU/ANE instead of CPU_ONLY."""
            from pathlib import Path
            from typing import cast
            import logging as log

            present = []

            # Try TensorFlow first
            try:
                import tensorflow as tf
                present.append("TensorFlow")
                try:
                    self.model_type = inference_module.Model.MODEL_TYPES.TENSORFLOW
                    self.model = tf.saved_model.load(str(model_path))
                    return
                except Exception as e:
                    if str(model_path).endswith("nmp"):
                        log.warning(
                            "Could not load TensorFlow model %s with error %s",
                            model_path,
                            e.__repr__(),
                        )
            except ImportError:
                pass

            # Try CoreML - USE ALL COMPUTE UNITS (GPU + ANE + CPU)
            try:
                present.append("CoreML")
                try:
                    self.model_type = inference_module.Model.MODEL_TYPES.COREML
                    # PATCHED: Use ALL compute units instead of CPU_ONLY
                    self.model = ct.models.MLModel(str(model_path), compute_units=ct.ComputeUnit.ALL)
                    logger.info("Loaded CoreML model with GPU/ANE acceleration")
                    return
                except Exception as e:
                    if str(model_path).endswith(".mlpackage"):
                        log.warning(
                            "Could not load CoreML file %s with error %s",
                            model_path,
                            e.__repr__(),
                        )
            except ImportError:
                pass

            # Try TFLite
            try:
                import ai_edge_litert.interpreter as tflite
                present.append("TFLite")
                try:
                    self.model_type = inference_module.Model.MODEL_TYPES.TFLITE
                    self.model = tflite.Interpreter(str(model_path))
                    return
                except Exception as e:
                    if str(model_path).endswith(".tflite"):
                        log.warning(
                            "Could not load TFLite file %s with error %s",
                            model_path,
                            e.__repr__(),
                        )
            except ImportError:
                pass

            # Try ONNX
            try:
                import onnxruntime as ort
                present.append("ONNX")
                try:
                    self.model_type = inference_module.Model.MODEL_TYPES.ONNX
                    self.model = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
                    return
                except Exception as e:
                    if str(model_path).endswith(".onnx"):
                        log.warning(
                            "Could not load ONNX file %s with error %s",
                            model_path,
                            e.__repr__(),
                        )
            except ImportError:
                pass

            raise ValueError(
                f"File {model_path} cannot be loaded into either "
                "TensorFlow, CoreML, TFLite or ONNX."
            )

        def patched_predict(self, x):
            """Patched predict that suppresses CoreML debug output."""
            from typing import cast

            if self.model_type == inference_module.Model.MODEL_TYPES.TENSORFLOW:
                import tensorflow as tf
                return {k: v.numpy() for k, v in cast(tf.keras.Model, self.model(x)).items()}

            elif self.model_type == inference_module.Model.MODEL_TYPES.COREML:
                # PATCHED: Removed debug prints
                result = cast(ct.models.MLModel, self.model).predict({"input_2": x})
                return {
                    "note": result["Identity_1"],
                    "onset": result["Identity_2"],
                    "contour": result["Identity"],
                }

            elif self.model_type == inference_module.Model.MODEL_TYPES.TFLITE:
                return self.model(input_2=x)

            elif self.model_type == inference_module.Model.MODEL_TYPES.ONNX:
                import onnxruntime as ort
                return {
                    k: v
                    for k, v in zip(
                        ["note", "onset", "contour"],
                        cast(ort.InferenceSession, self.model).run(
                            [
                                "StatefulPartitionedCall:1",
                                "StatefulPartitionedCall:2",
                                "StatefulPartitionedCall:0",
                            ],
                            {"serving_default_input_2:0": x},
                        ),
                    )
                }
            else:
                raise ValueError(f"Unknown model type: {self.model_type}")

        # Apply both patches
        inference_module.Model._load_model = patched_load_model
        inference_module.Model.predict = patched_predict
        _patched = True
        logger.info("Applied basic-pitch patches for GPU acceleration and debug suppression")

    except ImportError:
        # basic-pitch not installed, nothing to patch
        pass
    except Exception as e:
        logger.warning(f"Could not patch basic-pitch: {e}")


# Auto-apply when imported
apply_patch()
