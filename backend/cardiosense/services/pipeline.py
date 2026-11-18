"""Bridge to the pipeline core (``cardiosense.core``, CONTRACT §3).

The API layer never imports the core directly: it asks this provider for stream processors. That keeps one
place for rules-only mode, model metadata and (in tests) substitution of the processor factory.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from cardiosense.config import get_settings

log = logging.getLogger("cardiosense.pipeline")

FALLBACK_RULE_VERSION = "rules-1.0.0"


class StreamProcessorLike(Protocol):
    model_version: str | None
    rule_version: str

    def push(self, samples: Any, t0: int, lead_off: bool = False, annotations: list[dict] | None = None) -> Any: ...

    def flush(self) -> Any: ...

    def stats(self) -> dict[str, Any]: ...


ProcessorFactory = Callable[..., StreamProcessorLike]


class PipelineProvider:
    def __init__(self) -> None:
        self.available = False
        self.error: str | None = None
        self.classifier: Any = None
        self.rule_version: str = FALLBACK_RULE_VERSION
        self.model_config: dict[str, Any] = {}
        self.default_rules: dict[str, Any] | None = None
        self._factory: ProcessorFactory | None = None
        self._model_version_override: str | None = None

    # ------------------------------------------------------------ loading

    async def load(self) -> None:
        """Import the core and load the classifier (in a worker thread: TorchScript loading is slow)."""
        settings = get_settings()
        self.model_config = self._read_model_config(settings.model_dir)
        if self._factory is not None:  # already installed (tests)
            return
        try:
            from cardiosense import core  # noqa: PLC0415 - optional at import time
        except Exception as exc:  # noqa: BLE001 - core missing or broken: API still serves history
            self.error = f"pipeline core unavailable: {type(exc).__name__}: {exc}"
            log.error(self.error)
            return
        self.rule_version = getattr(core, "RULE_VERSION", FALLBACK_RULE_VERSION)
        self.default_rules = _discover_default_rules(core)
        try:
            if settings.torch_threads > 0:
                import torch  # noqa: PLC0415

                torch.set_num_threads(settings.torch_threads)
        except Exception:  # noqa: BLE001 - torch absent means rules-only mode anyway
            pass
        try:
            self.classifier = await asyncio.to_thread(core.load_classifier, str(settings.model_dir))
        except Exception as exc:  # noqa: BLE001 - corrupt model files: degrade to rules-only
            log.error("classifier failed to load (%s); running in rules-only mode", type(exc).__name__)
            self.classifier = None
        self._factory = core.StreamProcessor
        self.available = True
        self.error = None
        log.info("pipeline core ready: rule_version=%s model_loaded=%s", self.rule_version, self.model_loaded)

    def install(
        self,
        factory: ProcessorFactory,
        *,
        rule_version: str = FALLBACK_RULE_VERSION,
        classifier: Any = None,
        model_version: str | None = None,
    ) -> None:
        """Use a specific processor factory (tests / alternative cores)."""
        self._factory = factory
        self.rule_version = rule_version
        self.classifier = classifier
        self._model_version_override = model_version
        self.available = True
        self.error = None

    @staticmethod
    def _read_model_config(model_dir: Path) -> dict[str, Any]:
        path = Path(model_dir) / "model_config.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------ use

    @property
    def model_loaded(self) -> bool:
        return self.classifier is not None

    @property
    def model_version(self) -> str | None:
        if self._model_version_override:
            return self._model_version_override
        if not self.model_loaded:
            return None
        return self.model_config.get("model_version")

    def create(
        self, *, fs: int, adc_zero: float, adc_gain: float, rules_config: dict[str, Any] | None
    ) -> StreamProcessorLike:
        if self._factory is None:
            raise RuntimeError(self.error or "pipeline core not loaded")
        return self._factory(
            fs=fs, classifier=self.classifier, adc_zero=adc_zero, adc_gain=adc_gain, rules_config=rules_config
        )


def _discover_default_rules(core: Any) -> dict[str, Any] | None:
    """Best effort: the contract does not name the core's default-thresholds export."""
    try:
        from cardiosense.core.config import RulesConfig  # noqa: PLC0415

        return RulesConfig().to_dict()
    except Exception:  # noqa: BLE001 - older/alternative core layout: fall through to name probing
        pass
    for name in ("DEFAULT_RULES_CONFIG", "DEFAULT_RULES", "DEFAULT_RULE_CONFIG", "DEFAULT_CONFIG"):
        value = getattr(core, name, None)
        if isinstance(value, dict):
            return dict(value)
    for name in ("default_rules_config", "default_rules", "default_config"):
        fn = getattr(core, name, None)
        if callable(fn):
            try:
                value = fn()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(value, dict):
                return dict(value)
    return None
