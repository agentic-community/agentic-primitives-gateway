from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateEvaluatorRequest(BaseModel):
    name: str
    evaluator_type: str = "TRACE"
    config: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class UpdateEvaluatorRequest(BaseModel):
    config: dict[str, Any] | None = None
    description: str | None = None


class EvaluatorInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    evaluator_id: str
    status: str = ""
    created_at: datetime | str = ""


class ListEvaluatorsResponse(BaseModel):
    evaluators: list[EvaluatorInfo] = Field(default_factory=list)
    next_token: str | None = None


class EvaluateRequest(BaseModel):
    evaluator_id: str
    target: str | None = None
    input_data: str | None = None
    output_data: str | None = None
    expected_output: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: float | None = None
    label: str | None = None
    explanation: str | None = None
    evaluator_id: str = ""


class EvaluateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    evaluation_results: list[EvaluationResult] = Field(default_factory=list)


class CreateScoreRequest(BaseModel):
    name: str
    value: float | str
    trace_id: str | None = None
    observation_id: str | None = None
    comment: str | None = None
    data_type: str | None = None
    config_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoreInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    score_id: str
    name: str = ""
    value: float | str | None = None
    trace_id: str | None = None
    comment: str | None = None
    data_type: str | None = None


class ListScoresResponse(BaseModel):
    scores: list[ScoreInfo] = Field(default_factory=list)
    page: int = 1
    total_items: int | None = None


class CreateOnlineEvalConfigRequest(BaseModel):
    name: str
    evaluator_ids: list[str]
    config: dict[str, Any] = Field(default_factory=dict)


class OnlineEvalConfigInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    online_evaluation_config_id: str
    status: str = ""
    created_at: datetime | str = ""


class ListOnlineEvalConfigsResponse(BaseModel):
    online_evaluation_configs: list[OnlineEvalConfigInfo] = Field(default_factory=list)
    next_token: str | None = None
