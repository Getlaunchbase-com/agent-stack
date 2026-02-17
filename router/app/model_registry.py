"""Model registry — versioned model management for blueprint symbol detection.

Provides a registry of detection models with:
  - Version tracking (semver-style)
  - Per-class confidence thresholds
  - Model metadata (architecture, input size, class list)
  - Active model selection
  - Forward-compatible schema for adding new models

Models are registered declaratively; actual weights are loaded lazily
from the models/ directory or downloaded on first use.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    """Specification for a detection model."""

    model_id: str                    # e.g. "yolov8n-blueprint-v1"
    version: str                     # e.g. "1.0.0"
    architecture: str                # e.g. "YOLOv8n"
    input_size: int                  # e.g. 640
    classes: list[str]               # ordered class list
    default_threshold: float         # global confidence threshold
    class_thresholds: dict[str, float] = field(default_factory=dict)
    description: str = ""
    weights_path: str = ""           # relative to models/ dir
    trainable: bool = True

    def threshold_for(self, class_name: str) -> float:
        """Get the confidence threshold for a specific class."""
        return self.class_thresholds.get(class_name, self.default_threshold)

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "architecture": self.architecture,
            "input_size": self.input_size,
            "classes": list(self.classes),
            "class_count": len(self.classes),
            "default_threshold": self.default_threshold,
            "class_thresholds": dict(self.class_thresholds),
            "description": self.description,
            "weights_path": self.weights_path,
            "trainable": self.trainable,
        }


# =====================================================================
# Pre-registered models
# =====================================================================

# Blueprint symbol classes — low-voltage and electrical
_LV_CLASSES = [
    "data_drop",
    "wireless_ap",
    "cctv_camera",
    "card_reader",
    "door_contact",
    "fire_alarm",
    "smoke_detector",
    "pull_station",
    "horn_strobe",
    "speaker",
    "intercom",
    "outlet",
    "switch",
    "light_fixture",
    "junction_box",
    "panel",
    "conduit",
    "cable_tray",
]

_MODELS: dict[str, ModelSpec] = {}


def _register_builtin_models() -> None:
    """Register the built-in model specs."""

    # v1: YOLOv8n baseline — small, fast
    register_model(ModelSpec(
        model_id="yolov8n-blueprint-v1",
        version="1.0.0",
        architecture="YOLOv8n",
        input_size=640,
        classes=_LV_CLASSES,
        default_threshold=0.25,
        class_thresholds={
            "conduit": 0.35,       # conduit has more false positives
            "cable_tray": 0.35,
            "junction_box": 0.30,
        },
        description="YOLOv8 nano baseline for blueprint symbol detection. "
                    "Optimized for speed on CPU inference.",
        weights_path="models/yolov8n-blueprint-v1.pt",
        trainable=True,
    ))

    # v2: YOLOv8s improved — larger, more accurate
    register_model(ModelSpec(
        model_id="yolov8s-blueprint-v2",
        version="2.0.0",
        architecture="YOLOv8s",
        input_size=640,
        classes=_LV_CLASSES,
        default_threshold=0.30,
        class_thresholds={
            "conduit": 0.40,
            "cable_tray": 0.40,
            "junction_box": 0.35,
            "light_fixture": 0.30,
        },
        description="YOLOv8 small model with improved accuracy. "
                    "Recommended for production use when GPU is available.",
        weights_path="models/yolov8s-blueprint-v2.pt",
        trainable=True,
    ))

    # v3: YOLOv8m for maximum accuracy
    register_model(ModelSpec(
        model_id="yolov8m-blueprint-v3",
        version="3.0.0",
        architecture="YOLOv8m",
        input_size=1280,
        classes=_LV_CLASSES,
        default_threshold=0.35,
        class_thresholds={
            "conduit": 0.45,
            "cable_tray": 0.45,
        },
        description="YOLOv8 medium model for maximum accuracy on high-res blueprints. "
                    "Requires GPU for reasonable inference times.",
        weights_path="models/yolov8m-blueprint-v3.pt",
        trainable=True,
    ))


# =====================================================================
# Registry API
# =====================================================================

def register_model(spec: ModelSpec) -> None:
    """Register a model spec in the registry."""
    _MODELS[spec.model_id] = spec
    logger.info("Registered model: %s (v%s)", spec.model_id, spec.version)


def get_model(model_id: str) -> ModelSpec | None:
    """Get a model spec by ID."""
    return _MODELS.get(model_id)


def get_active_model() -> ModelSpec:
    """Get the currently active (default) model.

    Returns the v1 baseline by default, or the model specified
    by BLUEPRINT_ACTIVE_MODEL env var.
    """
    active_id = os.getenv("BLUEPRINT_ACTIVE_MODEL", "yolov8n-blueprint-v1")
    model = _MODELS.get(active_id)
    if model is None:
        # Fallback to first registered
        if _MODELS:
            return next(iter(_MODELS.values()))
        raise RuntimeError("No models registered in the model registry")
    return model


def list_models() -> list[dict]:
    """List all registered models."""
    return [spec.to_dict() for spec in _MODELS.values()]


def get_model_classes(model_id: str | None = None) -> list[str]:
    """Get the class list for a model (or the active model)."""
    if model_id:
        model = get_model(model_id)
        if model is None:
            return []
        return list(model.classes)
    return list(get_active_model().classes)


# Auto-register built-in models on import
_register_builtin_models()
