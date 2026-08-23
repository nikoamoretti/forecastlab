from __future__ import annotations


class ConfigurationError(ValueError):
    def __init__(self, reasons: list[str], message: str | None = None) -> None:
        self.reasons = reasons
        super().__init__(message or "; ".join(reasons))


class GraphForecastExecutionError(ConfigurationError):
    """A permanent, audit-recorded failure in the graph forecaster pipeline."""

    def __init__(
        self,
        reasons: list[str],
        *,
        stage: str,
        message: str | None = None,
    ) -> None:
        self.stage = stage
        super().__init__(reasons, message or f"Graph forecast execution failed at {stage}: {'; '.join(reasons)}")


class PermanentProviderError(RuntimeError):
    pass


class TransientProviderError(RuntimeError):
    pass


class EvidenceIntegrityError(RuntimeError):
    pass


class StructuredOutputError(ValueError):
    pass


class BudgetExceeded(RuntimeError):
    def __init__(self, stage: str, reason: str) -> None:
        self.stage = stage
        self.reason = reason
        super().__init__(f"Budget exceeded at {stage}: {reason}")


class ExperimentEnvironmentMismatch(ConfigurationError):
    def __init__(self, message: str) -> None:
        super().__init__(["experiment_environment_mismatch"], message)


def classify_http_status(status_code: int) -> type[Exception]:
    if status_code in {408, 429} or status_code >= 500:
        return TransientProviderError
    return PermanentProviderError
