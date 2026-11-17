"""Trial-level OpenTelemetry export for the local eval runner."""

from __future__ import annotations

import json
from collections.abc import Generator, Mapping
from contextlib import contextmanager

from opentelemetry import propagate
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span

from garden.trace_contract import GARDEN_EVAL_RUN_ID, GARDEN_EVAL_TRIAL_ID

_SERVICE_NAME = "garden-agent-evals"
_EXPORT_TIMEOUT_SECONDS = 1
_FLUSH_TIMEOUT_MILLISECONDS = 1_000


def _headers(environ: Mapping[str, str], signal: str) -> dict[str, str]:
    value = environ.get(f"OTEL_EXPORTER_OTLP_{signal}_HEADERS") or environ.get(
        "OTEL_EXPORTER_OTLP_HEADERS", ""
    )
    headers: dict[str, str] = {}
    for item in value.split(","):
        name, separator, header_value = item.strip().partition("=")
        if separator and name:
            headers[name.strip()] = header_value.strip()
    return headers


def _endpoint(environ: Mapping[str, str], signal: str) -> str | None:
    specific = environ.get(f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT")
    if specific:
        return specific
    generic = environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if generic:
        return f"{generic.rstrip('/')}/v1/{signal.lower()}"
    traces = environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if signal == "LOGS" and traces and traces.endswith("/v1/traces"):
        return f"{traces[: -len('/v1/traces')]}/v1/logs"
    return None


class EvalTelemetry:
    """Collect each trial under a named span and export its record as a correlated log."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        resource = Resource.create({"service.name": _SERVICE_NAME})
        self._tracer_provider = TracerProvider(resource=resource)
        self._logger_provider = LoggerProvider(resource=resource)
        self._logger = self._logger_provider.get_logger("garden.agent.evals")
        self._export_failed = False

        trace_endpoint = _endpoint(environ, "TRACES")
        if trace_endpoint:
            try:
                exporter = OTLPSpanExporter(
                    endpoint=trace_endpoint,
                    headers=_headers(environ, "TRACES"),
                    timeout=_EXPORT_TIMEOUT_SECONDS,
                )
                self._tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
            except Exception:
                self._export_failed = True

        logs_endpoint = _endpoint(environ, "LOGS")
        if logs_endpoint:
            try:
                headers = _headers(environ, "LOGS")
                headers.setdefault("stream-name", "garden-eval-trials")
                exporter = OTLPLogExporter(
                    endpoint=logs_endpoint,
                    headers=headers,
                    timeout=_EXPORT_TIMEOUT_SECONDS,
                )
                self._logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
            except Exception:
                self._export_failed = True

        self._tracer = self._tracer_provider.get_tracer(_SERVICE_NAME)

    @contextmanager
    def trial_span(
        self,
        *,
        run_id: str,
        trial_id: str,
        task_id: str,
        suite: str,
        area: str,
        model: str,
        repeat: int,
    ) -> Generator[Span, None, None]:
        attributes: dict[str, str | int] = {
            GARDEN_EVAL_RUN_ID: run_id,
            GARDEN_EVAL_TRIAL_ID: trial_id,
            "garden.eval.task_id": task_id,
            "garden.eval.suite": suite,
            "garden.eval.area": area,
            "gen_ai.request.model": model,
            "garden.eval.repeat": repeat,
        }
        with self._tracer.start_as_current_span(
            f"{task_id} / {model} / {repeat}", attributes=attributes
        ) as span:
            yield span

    @staticmethod
    def trace_id(span: Span) -> str | None:
        context = span.get_span_context()
        if not context.is_valid:
            return None
        return f"{context.trace_id:032x}"

    @staticmethod
    def traceparent() -> str | None:
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        return carrier.get("traceparent")

    def record_trial(self, record: dict[str, object]) -> None:
        attributes: dict[str, bool | float | int | str] = {}
        for key, value in record.items():
            if isinstance(value, (bool, float, int, str)):
                attributes[key] = value
            elif value is not None:
                attributes[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        try:
            self._logger.emit(
                severity_number=SeverityNumber.INFO,
                severity_text="INFO",
                body=json.dumps(record, ensure_ascii=False, sort_keys=True),
                attributes=attributes,
            )
        except Exception:
            self._export_failed = True

    def flush(self) -> bool:
        for provider in (self._logger_provider, self._tracer_provider):
            try:
                if not provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MILLISECONDS):
                    self._export_failed = True
            except Exception:
                self._export_failed = True
        for provider in (self._logger_provider, self._tracer_provider):
            try:
                provider.shutdown()
            except Exception:
                self._export_failed = True
        return not self._export_failed
