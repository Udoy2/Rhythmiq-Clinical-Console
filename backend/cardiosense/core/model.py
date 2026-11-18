"""Bridge to the offline inference module `ml/cardiosense_beats.py`.

The module is loaded from `<repo>/ml` (override: env `CARDIOSENSE_ML_DIR`) so
callers never touch `sys.path`. Its maths is imported, never copied.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import numpy as np

DEFAULT_MODEL_CFG = {
    "fs": 250, "pre": 90, "post": 160, "margin": 8, "template_prev_beats": 20,
    "bandpass_hz": [0.5, 40.0], "filter_order": 3, "class_bias": [0.0, 0.0, 0.0, 0.0],
    "unknown_threshold": 0.5, "model_version": None, "classes": ["N", "S", "V", "F"],
}
MODEL_FILE, CONFIG_FILE = "beat_model_ts.pt", "model_config.json"


def ml_dir() -> Path:
    env = os.environ.get("CARDIOSENSE_ML_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[3] / "ml"


def _load_beats_module():
    name = "cardiosense_beats"
    if name in sys.modules:
        return sys.modules[name]
    path = ml_dir() / "cardiosense_beats.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None or not path.is_file():
        raise ImportError(f"cardiosense_beats.py not found in {path.parent} (set CARDIOSENSE_ML_DIR)")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


cb = _load_beats_module()


def default_model_dir() -> Path:
    env = os.environ.get("CARDIOSENSE_MODEL_DIR")
    return Path(env) if env else ml_dir() / "models" / "cardiosense_beat_model"


def load_classifier(model_dir=None):
    """Return a `cardiosense_beats.BeatClassifier`, or None (rules-only mode) if the model files are missing."""
    d = Path(model_dir) if model_dir else default_model_dir()
    model, config = d / MODEL_FILE, d / CONFIG_FILE
    if not (model.is_file() and config.is_file()):
        return None
    import torch
    # beats arrive one at a time; intra-op threads only add contention (override with CARDIOSENSE_TORCH_THREADS)
    torch.set_num_threads(int(os.environ.get("CARDIOSENSE_TORCH_THREADS", "1")))
    clf = cb.BeatClassifier(str(model), str(config))
    clf.cfg = {**DEFAULT_MODEL_CFG, **clf.cfg}
    L = int(clf.cfg["pre"]) + int(clf.cfg["post"])
    for _ in range(3):      # TorchScript optimises during its first calls: keep that out of the live path
        clf.predict_inputs(np.zeros((1, 2, L), np.float32), np.zeros((1, 7), np.float32))
    return clf
