"""Export the fleet's traces to Cloud Trace.

**ADK already emits the spans. This only routes them somewhere a judge can look.**
`google.adk.workflow._node_runner` opens an `invoke_node <name>` span per node and an
`invoke_workflow <name>` span around the traversal, so the agent-hop Gantt is a
property of the graph rather than something narrated over it. Without an exporter
those spans are created and dropped, which is the "structure present, force absent"
shape this project keeps finding: instrumentation that exists and reaches nothing.

**Configured, not merely imported.** An import proves nothing. `is_exporting()` reports
whether a real span processor was installed, and the fact sheet computes its claim from
that rather than from the presence of the dependency.

**Silent in tests and local runs, by construction.** Exporting requires a project id and
credentials. Rather than failing or, worse, attempting network calls from the test suite,
this refuses to configure and SAYS WHY, and the reason is readable through
`why_not_exporting()` so a surface can report it instead of implying telemetry is on.
"""

from __future__ import annotations

import os
from typing import Final

import structlog
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

log = structlog.get_logger(__name__)

#: The service name every span is grouped under in Cloud Trace.
SERVICE_NAME: Final = "curtail-console-api"

#: Set to any non-empty value to keep tracing off where a project id happens to be
#: present. The test suite sets it, because a suite that exports spans to a real
#: backend is a suite that fails when somebody runs it on a plane.
DISABLE_ENV: Final = "CURTAIL_DISABLE_TRACING"

#: Attribute carrying the correlation id already threaded through the fleet, the event
#: log and the Pub/Sub message attributes. Naming it here means one string, so a trace
#: and a log line can be filtered by the same value.
CORRELATION_ATTRIBUTE: Final = "curtail.correlation_id"

_state: dict[str, str | None] = {"reason": "configure_tracing has not been called"}


def why_not_exporting() -> str | None:
    """The reason spans are not leaving this process, or None when they are.

    A surface that says nothing about telemetry when telemetry is off lets a reader
    assume it is on. This is what the fact sheet and the API report instead.
    """
    return _state["reason"]


def is_exporting() -> bool:
    return _state["reason"] is None


def configure_tracing(*, project_id: str | None = None) -> bool:
    """Install a Cloud Trace exporter, once. Returns whether spans now leave.

    **Idempotent on purpose.** Cloud Run can import a module more than once across
    workers, and installing a second `BatchSpanProcessor` would duplicate every span,
    which reads as the fleet running twice.

    Refuses rather than raises when it cannot export. A telemetry failure must never
    take down the surface that serves the curtailment recommendation: observability is
    a governance requirement, not a precondition for answering.
    """
    if is_exporting():
        return True

    if os.environ.get(DISABLE_ENV):
        _state["reason"] = f"{DISABLE_ENV} is set, so no spans are exported"
        return False

    project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        _state["reason"] = "GOOGLE_CLOUD_PROJECT is not set, so no spans are exported"
        return False

    try:
        # Untyped in the exporter package, like ADK's session services elsewhere here.
        exporter = CloudTraceSpanExporter(project_id=project)  # type: ignore[no-untyped-call]
        provider = TracerProvider(
            resource=Resource.create({"service.name": SERVICE_NAME, "cloud.project": project})
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    except Exception as exc:  # pragma: no cover - only on a broken environment
        # Deliberately broad and deliberately non-fatal. Every failure mode here is an
        # environment problem, and none of them is a reason to stop serving.
        _state["reason"] = f"the Cloud Trace exporter could not be installed: {exc}"
        log.warning("tracing not configured", reason=_state["reason"])
        return False

    _state["reason"] = None
    log.info("tracing configured", project=project, service=SERVICE_NAME)
    return True


def flush(timeout_millis: int = 5000) -> None:
    """Push pending spans now.

    `BatchSpanProcessor` batches, and a Cloud Run container can go idle before a batch
    is due. On a live demo that means the traversal a judge just ran is not in Cloud
    Trace when they look. Flushing at the end of a request costs a moment and makes the
    trace present when it is wanted.
    """
    provider = trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    if force_flush is not None:
        force_flush(timeout_millis)


def tracer() -> trace.Tracer:
    return trace.get_tracer("curtail")
