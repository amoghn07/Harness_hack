"""Optional Langfuse tracing for the remediation analyzer.

`LangfuseAnalyzer` wraps any `BedrockAnalyzerBase` (mock or real). On each call
it records the generation (input brief, output plan, model, latency) and the
deterministic eval scores (groundedness / coverage / schema_validity) to
Langfuse, then returns the analysis unchanged.

It is strictly observability: the gate in the orchestrator computes `evaluate()`
itself, so tracing being off — or Langfuse being uninstalled or misconfigured —
never changes behavior. Every Langfuse call is guarded; on any failure the
wrapper logs once and falls through to the inner analyzer.

Activate with REPOHEALTH_TRACING=langfuse (+ LANGFUSE_* keys).
"""

from __future__ import annotations

import logging

from .bedrock import Analysis, BedrockAnalyzerBase, _detection_brief
from .config import Config
from .detect import Detection
from .evaluate import evaluate

log = logging.getLogger(__name__)


class LangfuseAnalyzer(BedrockAnalyzerBase):
    def __init__(self, inner: BedrockAnalyzerBase, cfg: Config) -> None:
        self.inner = inner
        self._client = self._build_client(cfg)

    @staticmethod
    def _build_client(cfg: Config):
        if not (cfg.langfuse_public_key and cfg.langfuse_secret_key):
            log.warning("REPOHEALTH_TRACING=langfuse but LANGFUSE_* keys are unset; "
                        "tracing disabled.")
            return None
        try:
            from langfuse import Langfuse  # type: ignore
        except ImportError:
            log.warning("langfuse is not installed (`pip install langfuse`); "
                        "tracing disabled.")
            return None
        try:
            return Langfuse(
                public_key=cfg.langfuse_public_key,
                secret_key=cfg.langfuse_secret_key,
                host=cfg.langfuse_host or None,
            )
        except Exception as exc:  # pragma: no cover - depends on env
            log.warning("Langfuse init failed (%s); tracing disabled.", exc)
            return None

    def analyze(self, detection: Detection) -> Analysis:
        analysis = self.inner.analyze(detection)
        if self._client is not None:
            self._record(detection, analysis)
        return analysis

    def _record(self, detection: Detection, analysis: Analysis) -> None:
        """Log the generation + eval scores. Fully guarded — never raises.

        NB: targets the Langfuse v3 SDK surface. The numbers (eval scores) are
        backend-independent; if a binding name drifts in your installed version,
        the except clause keeps the pipeline running and logs the mismatch."""
        result = evaluate(detection, analysis)
        try:
            gen = self._client.start_generation(
                name="remediation_plan",
                model=analysis.model,
                input=_detection_brief(detection),
                metadata={"repo": detection.repo, "score": detection.score},
            )
            gen.update(output={"summary": analysis.summary,
                               "actions": [vars(a) for a in analysis.actions]})
            gen.end()
            trace_id = getattr(gen, "trace_id", None)
            for name, value in result.as_scores().items():
                self._client.create_score(name=name, value=value, trace_id=trace_id)
            if not result.safe_to_act:
                self._client.create_score(
                    name="safe_to_act", value=0.0, trace_id=trace_id,
                    comment=f"ungrounded={result.ungrounded} invalid={result.invalid_actions}",
                )
            self._client.flush()
        except Exception as exc:  # pragma: no cover - depends on SDK version/env
            log.warning("Langfuse logging failed (%s); continuing.", exc)
