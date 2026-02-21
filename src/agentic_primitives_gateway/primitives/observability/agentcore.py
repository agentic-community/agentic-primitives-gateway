from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import boto3
from botocore.session import Session as BotocoreSession

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.primitives.observability.base import ObservabilityProvider

logger = logging.getLogger(__name__)


class AgentCoreObservabilityProvider(ObservabilityProvider):
    """Observability provider using ADOT to send traces to CloudWatch/X-Ray.

    Uses the AwsAuthSession from the AWS OpenTelemetry distro for SigV4-signed
    OTLP exports to the X-Ray endpoint. Traces appear in CloudWatch under
    Application Signals > Traces.

    Prerequisites:
      - ``pip install aws-opentelemetry-distro``
      - CloudWatch Transaction Search enabled in the account

    Provider config example::

        backend: agentic_primitives_gateway.primitives.observability.agentcore.AgentCoreObservabilityProvider
        config:
          region: "us-east-1"
          service_name: "agentic-primitives-gateway"
          agent_id: "agentic-primitives-gateway"
    """

    def __init__(
        self,
        region: str = "us-east-1",
        service_name: str = "agentic-primitives-gateway",
        agent_id: str = "agentic-primitives-gateway",
        **kwargs: Any,
    ) -> None:
        self._region = region
        self._service_name = service_name
        self._agent_id = agent_id
        self._log_group = f"/aws/bedrock-agentcore/runtimes/{agent_id}"

        self._ensure_log_group()
        self._tracer = self._setup_tracer()
        logger.info(
            "AgentCore observability provider initialized (region=%s, service=%s, log_group=%s)",
            region,
            service_name,
            self._log_group,
        )

    def _ensure_log_group(self) -> None:
        """Create the CloudWatch log group if it doesn't exist."""
        try:
            logs = boto3.client("logs", region_name=self._region)
            logs.create_log_group(logGroupName=self._log_group)
            logger.info("Created log group: %s", self._log_group)
        except Exception as e:
            if "ResourceAlreadyExistsException" in type(e).__name__:
                logger.debug("Log group already exists: %s", self._log_group)
            else:
                logger.warning("Could not create log group %s: %s", self._log_group, e)

    def _setup_tracer(self) -> Any:
        """Configure OTel tracer with SigV4-signed OTLP exporter for X-Ray."""
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": self._service_name,
                "aws.log.group.names": self._log_group,
            }
        )

        provider_kwargs: dict[str, Any] = {"resource": resource}
        try:
            from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator

            provider_kwargs["id_generator"] = AwsXRayIdGenerator()
        except ImportError:
            pass

        tracer_provider = TracerProvider(**provider_kwargs)

        # Use the ADOT OTLPAwsSpanExporter which handles SigV4 and the correct endpoint
        try:
            from amazon.opentelemetry.distro.exporter.otlp.aws.traces.otlp_aws_span_exporter import (
                OTLPAwsSpanExporter,
            )

            botocore_session = BotocoreSession()
            endpoint = f"https://xray.{self._region}.amazonaws.com/v1/traces"
            exporter = OTLPAwsSpanExporter(
                aws_region=self._region,
                session=botocore_session,
                endpoint=endpoint,
            )
            logger.info("ADOT OTLPAwsSpanExporter configured (region=%s)", self._region)
        except ImportError:
            logger.warning(
                "aws-opentelemetry-distro not installed; falling back to plain OTLP. "
                "Install with: pip install aws-opentelemetry-distro"
            )
            exporter = OTLPSpanExporter(endpoint=f"https://xray.{self._region}.amazonaws.com")

        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(tracer_provider)
        return trace.get_tracer(self._service_name)

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def ingest_trace(self, trace_data: dict[str, Any]) -> None:
        def _ingest() -> None:
            from opentelemetry import baggage, context

            ctx = context.get_current()
            if trace_data.get("session_id"):
                ctx = baggage.set_baggage("session.id", trace_data["session_id"], ctx)
            token = context.attach(ctx)

            try:
                with self._tracer.start_as_current_span(
                    trace_data.get("name", trace_data.get("trace_id", "trace"))
                ) as root_span:
                    root_span.set_attribute("trace.id", trace_data.get("trace_id", ""))
                    if trace_data.get("user_id"):
                        root_span.set_attribute("user.id", trace_data["user_id"])
                    if trace_data.get("session_id"):
                        root_span.set_attribute("session.id", trace_data["session_id"])
                    if trace_data.get("input"):
                        root_span.set_attribute("input", str(trace_data["input"]))
                    if trace_data.get("output"):
                        root_span.set_attribute("output", str(trace_data["output"]))
                    for key, value in trace_data.get("metadata", {}).items():
                        root_span.set_attribute(f"metadata.{key}", str(value))
                    for tag in trace_data.get("tags", []):
                        root_span.set_attribute(f"tag.{tag}", True)

                    for span_data in trace_data.get("spans", []):
                        with self._tracer.start_as_current_span(span_data.get("name", "span")) as child_span:
                            if span_data.get("input"):
                                child_span.set_attribute("input", str(span_data["input"]))
                            if span_data.get("output"):
                                child_span.set_attribute("output", str(span_data["output"]))
                            if span_data.get("model"):
                                child_span.set_attribute("gen_ai.model", span_data["model"])
                            for key, value in span_data.get("metadata", {}).items():
                                child_span.set_attribute(f"metadata.{key}", str(value))
            finally:
                context.detach(token)

        await self._run_sync(_ingest)

    async def ingest_log(self, log_entry: dict[str, Any]) -> None:
        def _ingest() -> None:
            with self._tracer.start_as_current_span("log") as span:
                span.set_attribute("log.level", log_entry.get("level", "info"))
                span.set_attribute("log.message", log_entry.get("message", ""))
                for key, value in log_entry.get("metadata", {}).items():
                    span.set_attribute(f"metadata.{key}", str(value))
                span.add_event(
                    log_entry.get("message", "log"),
                    attributes={k: str(v) for k, v in log_entry.get("metadata", {}).items()},
                )

        await self._run_sync(_ingest)

    async def query_traces(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            session = get_boto3_session(default_region=self._region)
            xray = session.client("xray", region_name=session.region_name)

            try:
                f = filters or {}
                now = datetime.now(UTC)

                response = xray.get_trace_summaries(
                    StartTime=now - timedelta(hours=1),
                    EndTime=now,
                )
                traces = []
                for summary in response.get("TraceSummaries", []):
                    traces.append(
                        {
                            "trace_id": summary.get("Id", ""),
                            "name": (summary.get("EntryPoint") or {}).get("Name", ""),
                            "metadata": {
                                "duration": summary.get("Duration"),
                                "response_time": summary.get("ResponseTime"),
                                "has_fault": summary.get("HasFault"),
                                "has_error": summary.get("HasError"),
                            },
                        }
                    )
                return traces[: f.get("limit", 100)]
            except Exception:
                logger.exception("Failed to query X-Ray traces")
                return []

        result: list[dict[str, Any]] = await self._run_sync(_query)
        return result

    async def healthcheck(self) -> bool:
        return self._tracer is not None
