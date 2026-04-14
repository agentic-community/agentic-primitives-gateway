from __future__ import annotations

import logging
import os
from typing import Any

from langfuse.api.client import FernLangfuse
from langfuse.api.resources.commons.types.score_data_type import ScoreDataType
from langfuse.api.resources.score.types.create_score_request import CreateScoreRequest
from langfuse.api.resources.score_configs.types.create_score_config_request import CreateScoreConfigRequest
from langfuse.api.resources.score_configs.types.update_score_config_request import UpdateScoreConfigRequest

from agentic_primitives_gateway.context import get_service_credentials_or_defaults
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.evaluations.base import EvaluationsProvider

logger = logging.getLogger(__name__)

# Map our evaluator_type strings to Langfuse ScoreDataType
_DATA_TYPE_MAP: dict[str, ScoreDataType] = {
    "numeric": ScoreDataType.NUMERIC,
    "boolean": ScoreDataType.BOOLEAN,
    "categorical": ScoreDataType.CATEGORICAL,
}

_DATA_TYPE_STR_MAP: dict[str, str] = {
    "numeric": "NUMERIC",
    "boolean": "BOOLEAN",
    "categorical": "CATEGORICAL",
}


def _to_data_type(evaluator_type: str) -> ScoreDataType:
    """Convert evaluator_type to Langfuse ScoreDataType, defaulting to NUMERIC."""
    return _DATA_TYPE_MAP.get(evaluator_type.lower(), ScoreDataType.NUMERIC)


def _to_data_type_str(s: str | None) -> ScoreDataType | None:
    """Convert a string data_type to Langfuse ScoreDataType."""
    if s is None:
        return None
    return _DATA_TYPE_MAP.get(s.lower())


def _score_config_to_dict(config: Any) -> dict[str, Any]:
    """Convert a Langfuse ScoreConfig to our evaluator dict format."""
    return {
        "evaluator_id": config.id,
        "name": config.name,
        "evaluator_type": config.data_type.value if hasattr(config.data_type, "value") else str(config.data_type),
        "description": config.description or "",
        "status": "ARCHIVED" if config.is_archived else "ACTIVE",
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "config": {
            "min_value": config.min_value,
            "max_value": config.max_value,
            "categories": [{"label": c.label, "value": c.value} for c in (config.categories or [])],
        },
    }


def _score_to_dict(score: Any) -> dict[str, Any]:
    """Convert a Langfuse Score to our score dict format."""
    return {
        "score_id": score.id,
        "name": score.name,
        "value": score.value,
        "trace_id": getattr(score, "trace_id", None),
        "observation_id": getattr(score, "observation_id", None),
        "comment": getattr(score, "comment", None),
        "data_type": getattr(score, "data_type", None),
    }


class LangfuseEvaluationsProvider(SyncRunnerMixin, EvaluationsProvider):
    """Evaluations provider backed by Langfuse.

    Supports two distinct evaluation modes:

    - **evaluate()**: Runs an LLM-as-a-judge evaluation using Langfuse's
      ``run_batched_evaluation`` on a single trace. Creates a score from the
      evaluator function's output.
    - **Score CRUD**: Record, retrieve, and manage pre-computed scores via
      Langfuse's score API.

    Evaluators map to Langfuse Score Configs (definitions for score types).

    Langfuse credentials (public_key, secret_key, base_url) are read from
    request context on every call via the X-Cred-Langfuse-* headers. Falls
    back to config-level defaults.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.evaluations.langfuse.LangfuseEvaluationsProvider
        config:
          public_key: "pk-..."
          secret_key: "sk-..."
          base_url: "https://cloud.langfuse.com"
    """

    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._default_public_key = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY")
        self._default_secret_key = secret_key or os.environ.get("LANGFUSE_SECRET_KEY")
        self._default_base_url = base_url or os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
        logger.info("LangfuseEvaluationsProvider initialized")

    def _resolve_credentials(self) -> dict[str, Any]:
        return get_service_credentials_or_defaults(
            "langfuse",
            {
                "public_key": self._default_public_key,
                "secret_key": self._default_secret_key,
                "base_url": self._default_base_url,
            },
        )

    def _resolve_rest_client(self) -> FernLangfuse:
        """Create a Langfuse REST API client with per-request credentials."""
        creds = self._resolve_credentials()
        return FernLangfuse(
            base_url=creds.get("base_url", "https://cloud.langfuse.com"),
            username=creds.get("public_key", ""),
            password=creds.get("secret_key", ""),
        )

    def _resolve_sdk_client(self) -> Any:
        """Create a high-level Langfuse SDK client for evaluate operations."""
        from langfuse import Langfuse

        creds = self._resolve_credentials()
        return Langfuse(
            public_key=creds.get("public_key"),
            secret_key=creds.get("secret_key"),
            base_url=creds.get("base_url"),
        )

    # ── Evaluator CRUD (backed by Langfuse Score Configs) ─────────────

    async def create_evaluator(
        self,
        name: str,
        evaluator_type: str,
        config: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        client = self._resolve_rest_client()
        cfg = config or {}

        def _create() -> dict[str, Any]:
            kwargs: dict[str, Any] = {
                "name": name,
                "data_type": _to_data_type(evaluator_type),
            }
            if description:
                kwargs["description"] = description
            if cfg.get("min_value") is not None:
                kwargs["min_value"] = cfg["min_value"]
            if cfg.get("max_value") is not None:
                kwargs["max_value"] = cfg["max_value"]
            if cfg.get("categories"):
                kwargs["categories"] = cfg["categories"]
            req = CreateScoreConfigRequest(**kwargs)
            result = client.score_configs.create(request=req)
            return _score_config_to_dict(result)

        return await self._run_sync(_create)

    async def get_evaluator(self, evaluator_id: str) -> dict[str, Any]:
        client = self._resolve_rest_client()

        def _get() -> dict[str, Any]:
            result = client.score_configs.get_by_id(evaluator_id)
            return _score_config_to_dict(result)

        return await self._run_sync(_get)

    async def update_evaluator(
        self,
        evaluator_id: str,
        config: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        client = self._resolve_rest_client()
        cfg = config or {}

        def _update() -> dict[str, Any]:
            kwargs: dict[str, Any] = {}
            if description is not None:
                kwargs["description"] = description
            if cfg.get("is_archived") is not None:
                kwargs["is_archived"] = cfg["is_archived"]
            req = UpdateScoreConfigRequest(**kwargs)
            result = client.score_configs.update(evaluator_id, request=req)
            return _score_config_to_dict(result)

        return await self._run_sync(_update)

    async def delete_evaluator(self, evaluator_id: str) -> None:
        """Archive the score config (Langfuse does not support hard delete)."""
        client = self._resolve_rest_client()

        def _archive() -> None:
            req = UpdateScoreConfigRequest(is_archived=True)
            client.score_configs.update(evaluator_id, request=req)

        await self._run_sync(_archive)

    async def list_evaluators(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        client = self._resolve_rest_client()
        page = int(next_token) if next_token else 1

        def _list() -> dict[str, Any]:
            result = client.score_configs.get(page=page, limit=max_results)
            configs = [_score_config_to_dict(c) for c in result.data]
            has_more = len(configs) == max_results
            return {
                "evaluators": configs,
                "next_token": str(page + 1) if has_more else None,
            }

        return await self._run_sync(_list)

    # ── Evaluate (record a score in Langfuse) ───────────────────────────

    async def evaluate(
        self,
        evaluator_id: str,
        target: str | None = None,
        input_data: str | None = None,
        output_data: str | None = None,
        expected_output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an evaluation score in Langfuse.

        For LLM-as-a-judge, configure evaluators in the Langfuse UI with
        LLM connections — Langfuse runs evaluations automatically on traces.
        See: https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge

        This method records a score (pre-computed or manual) against a trace.

        Args:
            evaluator_id: Score config ID or score name.
            target: Trace ID to associate the score with.
            input_data: The input/question (stored in metadata).
            output_data: The model's output (stored as comment).
            expected_output: The reference output (stored in metadata).
            metadata: Additional context. ``metadata.value`` sets the score
                (defaults to 1.0 if not provided).
        """
        client = self._resolve_sdk_client()
        meta = dict(metadata or {})
        score_value: float | str = meta.pop("value", 1.0)
        comment = output_data
        if expected_output:
            comment = f"{output_data}\n\nExpected: {expected_output}" if output_data else expected_output

        def _evaluate() -> dict[str, Any]:
            client.create_score(
                name=evaluator_id,
                value=score_value,
                trace_id=target,
                config_id=evaluator_id if target else None,
                comment=comment,
                metadata={**meta, "input_data": input_data} if input_data else meta or None,
            )
            client.flush()
            return {
                "results": [
                    {
                        "value": score_value,
                        "evaluator_id": evaluator_id,
                        "trace_id": target,
                    }
                ],
                "evaluator_id": evaluator_id,
            }

        return await self._run_sync(_evaluate)

    # ── Score CRUD (record/retrieve pre-computed scores) ──────────────

    async def create_score(
        self,
        *,
        name: str,
        value: float | str,
        trace_id: str | None = None,
        observation_id: str | None = None,
        comment: str | None = None,
        data_type: str | None = None,
        config_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._resolve_rest_client()

        def _create() -> dict[str, Any]:
            kwargs: dict[str, Any] = {"name": name, "value": value}
            if trace_id is not None:
                kwargs["trace_id"] = trace_id
            if observation_id is not None:
                kwargs["observation_id"] = observation_id
            if comment is not None:
                kwargs["comment"] = comment
            dt = _to_data_type_str(data_type)
            if dt is not None:
                kwargs["data_type"] = dt
            if config_id is not None:
                kwargs["config_id"] = config_id
            if metadata:
                kwargs["metadata"] = metadata
            req = CreateScoreRequest(**kwargs)
            result = client.score.create(request=req)
            return {
                "score_id": result.id,
                "name": name,
                "value": value,
                "trace_id": trace_id,
                "comment": comment,
                "data_type": data_type,
            }

        return await self._run_sync(_create)

    async def get_score(self, score_id: str) -> dict[str, Any]:
        client = self._resolve_rest_client()

        def _get() -> dict[str, Any]:
            try:
                result = client.score_v_2.get_by_id(score_id)
            except Exception as e:
                if "404" in str(e) or "not found" in str(e).lower():
                    raise KeyError(f"Score not found: {score_id}") from e
                raise
            return _score_to_dict(result)

        return await self._run_sync(_get)

    async def delete_score(self, score_id: str) -> None:
        client = self._resolve_rest_client()

        def _delete() -> None:
            client.score.delete(score_id)

        await self._run_sync(_delete)

    async def list_scores(
        self,
        *,
        trace_id: str | None = None,
        name: str | None = None,
        config_id: str | None = None,
        data_type: str | None = None,
        page: int = 1,
        limit: int = 100,
    ) -> dict[str, Any]:
        client = self._resolve_rest_client()

        def _list() -> dict[str, Any]:
            result = client.score_v_2.get(
                page=page,
                limit=limit,
                trace_id=trace_id,
                name=name,
                config_id=config_id,
                data_type=_to_data_type_str(data_type),
            )
            scores = [_score_to_dict(s) for s in result.data]
            return {
                "scores": scores,
                "page": page,
                "total_items": getattr(result.meta, "total_items", None) if hasattr(result, "meta") else None,
            }

        return await self._run_sync(_list)

    # ── Healthcheck ───────────────────────────────────────────────────

    async def healthcheck(self) -> bool | str:
        """Check Langfuse connectivity."""
        try:
            from langfuse import Langfuse

            creds = self._resolve_credentials()
            has_keys = creds.get("public_key") and creds.get("secret_key")
            if has_keys:
                lf = Langfuse(
                    public_key=creds["public_key"],
                    secret_key=creds["secret_key"],
                    base_url=creds.get("base_url"),
                )
                if lf.auth_check():
                    return True
            # Fall back to HTTP ping
            import httpx

            base = creds.get("base_url") or self._default_base_url or "https://cloud.langfuse.com"
            resp = httpx.get(f"{base}/api/public/health", timeout=5)
            if resp.status_code == 200:
                return "reachable"
            return False
        except Exception:
            logger.debug("Langfuse evaluations healthcheck failed", exc_info=True)
            return False
