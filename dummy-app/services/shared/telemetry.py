import contextvars
import json
import logging
import sys
import time
from datetime import UTC, datetime
from uuid import uuid4

import psutil
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Observation
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

request_id_var = contextvars.ContextVar("request_id", default="system")


def utcnow():
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def correlation():
    context = trace.get_current_span().get_span_context()
    return {
        "request_id": request_id_var.get(),
        "trace_id": f"{context.trace_id:032x}" if context.is_valid else None,
        "span_id": f"{context.span_id:016x}" if context.is_valid else None,
    }


def current_traceparent():
    """W3C traceparent of the current span, or None outside a recorded trace."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"00-{context.trace_id:032x}-{context.span_id:016x}-{context.trace_flags:02x}"


def link_to(traceparent):
    """Span links pointing at a stored traceparent (none when it is missing)."""
    if not traceparent:
        return []
    _, trace_id, span_id, flags = traceparent.split("-")
    context = trace.SpanContext(
        int(trace_id, 16), int(span_id, 16), is_remote=True, trace_flags=trace.TraceFlags(int(flags, 16))
    )
    return [trace.Link(context)]


class JsonFormatter(logging.Formatter):
    def __init__(self, service, environment):
        super().__init__()
        self.service, self.environment = service, environment

    def format(self, record):
        fields = getattr(record, "structured", None)
        if fields is None:
            # Third-party errors must remain JSON too; exclude arbitrary exception text/URLs/DSNs.
            fields = {"event": "process_diagnostic", "message": record.name, **correlation()}
        return json.dumps(
            {
                "timestamp": utcnow(),
                "level": "WARN" if record.levelno == 30 else record.levelname,
                "service.name": self.service,
                "environment": self.environment,
                **fields,
            },
            separators=(",", ":"),
            default=str,
        )


class JsonOTLPHandler(LoggingHandler):
    @staticmethod
    def _get_attributes(record):
        return {
            key: value for key, value in LoggingHandler._get_attributes(record).items() if key != "structured"
        }


class Telemetry:
    def __init__(self, service, settings, *, span_exporter=None, metric_reader=None, log_exporter=None):
        self.service = service
        self.environment = settings.environment
        self.resource = Resource.create(
            {
                "service.name": service,
                "service.version": "v1",
                "deployment.environment": settings.environment,
                "deployment.id": f"boot-{uuid4()}",
            }
        )
        self.traces = TracerProvider(
            resource=self.resource, sampler=ParentBased(TraceIdRatioBased(settings.sample_ratio))
        )
        readers = [metric_reader] if metric_reader else []
        self.logs = LoggerProvider(resource=self.resource)
        endpoint = settings.otlp_endpoint.rstrip("/")
        if endpoint:
            span_exporter = span_exporter or OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=1)
            log_exporter = log_exporter or OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", timeout=1)
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=1),
                    export_interval_millis=3000,
                    export_timeout_millis=1500,
                )
            )
        if span_exporter:
            self.traces.add_span_processor(
                BatchSpanProcessor(
                    span_exporter,
                    max_queue_size=2048,
                    schedule_delay_millis=500,
                )
            )
        if log_exporter:
            self.logs.add_log_record_processor(
                BatchLogRecordProcessor(
                    log_exporter,
                    max_queue_size=2048,
                    schedule_delay_millis=500,
                )
            )
        self.metrics = MeterProvider(resource=self.resource, metric_readers=readers)
        self.tracer = self.traces.get_tracer("observa.app")
        self.meter = self.metrics.get_meter("observa.app")
        self.logger = logging.getLogger(f"observa.{service}.{uuid4()}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(JsonFormatter(service, settings.environment))
        self.logger.addHandler(console)
        if log_exporter:
            self.logger.addHandler(JsonOTLPHandler(logger_provider=self.logs))
        self.counters = {}
        self.histograms = {}
        process = psutil.Process()
        self.meter.create_observable_gauge(
            "process.memory.usage", [lambda _: [Observation(process.memory_info().rss)]], unit="By"
        )
        self.meter.create_observable_counter(
            "process.cpu.time", [lambda _: [Observation(sum(process.cpu_times()[:2]))]], unit="s"
        )
        self.started = time.monotonic()
        self.meter.create_observable_gauge(
            "process.uptime", [lambda _: [Observation(time.monotonic() - self.started)]], unit="s"
        )

    def log(self, level, event, message, **fields):
        data = {
            "timestamp": utcnow(),
            "level": level,
            "service.name": self.service,
            "environment": self.environment,
            "event": event,
            "message": message,
            **correlation(),
            **fields,
        }
        # Flatten scalar attributes in OTLP as well as retaining the JSON body in Loki.
        attributes = {
            k: v for k, v in data.items() if k != "message" and isinstance(v, (str, int, float, bool))
        }
        self.logger.log(
            getattr(logging, "WARNING" if level == "WARN" else level),
            json.dumps(data),
            extra={"structured": data, **attributes},
        )

    def count(self, name, attributes=None, value=1):
        if name not in self.counters:
            self.counters[name] = self.meter.create_counter(name)
        self.counters[name].add(value, attributes or {})

    def duration(self, name, seconds, attributes=None):
        if name not in self.histograms:
            self.histograms[name] = self.meter.create_histogram(name, unit="s")
        self.histograms[name].record(seconds, attributes or {})

    def close(self):
        self.traces.shutdown()
        self.metrics.shutdown()
        self.logs.shutdown()
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
