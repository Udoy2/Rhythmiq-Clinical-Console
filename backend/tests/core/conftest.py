"""Fixtures for the pipeline-core tests: synthetic ECG, a fake TorchScript model, MIT-BIH records."""
import json
from pathlib import Path

import numpy as np
import pytest

from cardiosense.core import load_classifier
from cardiosense.core.bench import load_record
from cardiosense.core.model import default_model_dir

REPO = Path(__file__).resolve().parents[3]
RECORDS = REPO / "data" / "records"
_REPORT: list[str] = []


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: full-record runs (deselect with -m 'not slow')")


def pytest_terminal_summary(terminalreporter):
    if _REPORT:
        terminalreporter.write_sep("-", "pipeline core measurements")
        for line in _REPORT:
            terminalreporter.write_line(line)


@pytest.fixture
def report():
    """Lines appended here are printed in the terminal summary (also with -q)."""
    return _REPORT.append


@pytest.fixture(scope="session")
def fake_model_dir(tmp_path_factory):
    """Hand-made TorchScript module with the production signature (beat[B,2,250], rr[B,7]) -> probs[B,4]:
    V when the beat differs from the running template, S when it is premature, else N."""
    import torch

    class Fake(torch.nn.Module):
        def forward(self, beat: torch.Tensor, rr: torch.Tensor) -> torch.Tensor:
            dev = (beat[:, 1, :] ** 2).mean(1)
            prem = -rr[:, 3]
            z = torch.stack([torch.full_like(dev, 2.0), 8.0 * prem - 1.0 - 3.0 * dev, 6.0 * dev - 2.0,
                             torch.full_like(dev, -2.0)], 1)
            return torch.softmax(3.0 * z, 1)

    d = tmp_path_factory.mktemp("fake_model")
    torch.jit.script(Fake()).save(str(d / "beat_model_ts.pt"))
    (d / "model_config.json").write_text(json.dumps({
        "fs": 250, "pre": 90, "post": 160, "margin": 8, "template_prev_beats": 20, "bandpass_hz": [0.5, 40],
        "filter_order": 3, "class_bias": [0, 0, 0, 0], "unknown_threshold": 0.5,
        "model_version": "fake-test-model", "classes": ["N", "S", "V", "F"]}))
    return d


@pytest.fixture(scope="session")
def fake_clf(fake_model_dir):
    return load_classifier(fake_model_dir)


@pytest.fixture(scope="session")
def real_clf():
    """The model in ml/models/cardiosense_beat_model (or $CARDIOSENSE_MODEL_DIR); None if not present."""
    return load_classifier(default_model_dir())


@pytest.fixture(scope="session")
def record():
    cache = {}

    def get(name, seconds=None):
        path = RECORDS / f"{name}.csv"
        if not path.is_file():
            pytest.skip(f"{path} not available")
        if (name, seconds) not in cache:
            cache[(name, seconds)] = load_record(path, seconds)
        return cache[(name, seconds)]
    return get
