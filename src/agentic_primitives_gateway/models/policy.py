from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreatePolicyEngineRequest(BaseModel):
    name: str
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class PolicyEngineInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    policy_engine_id: str
    name: str = ""
    description: str = ""
    status: str = ""
    created_at: datetime | str = ""


class ListPolicyEnginesResponse(BaseModel):
    policy_engines: list[PolicyEngineInfo] = Field(default_factory=list)
    next_token: str | None = None


class CreatePolicyRequest(BaseModel):
    policy_body: str
    description: str = ""


class UpdatePolicyRequest(BaseModel):
    policy_body: str
    description: str | None = None


class PolicyInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    policy_id: str
    policy_engine_id: str = ""
    definition: dict[str, Any] | str = ""
    description: str = ""
    created_at: datetime | str = ""


class ListPoliciesResponse(BaseModel):
    policies: list[PolicyInfo] = Field(default_factory=list)
    next_token: str | None = None


class StartPolicyGenerationRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class PolicyGenerationInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    policy_generation_id: str = ""
    policy_engine_id: str = ""
    status: str = ""
    created_at: datetime | str = ""


class ListPolicyGenerationsResponse(BaseModel):
    policy_generations: list[PolicyGenerationInfo] = Field(default_factory=list)
    next_token: str | None = None


class PolicyGenerationAssetInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    asset_id: str = ""
    policy_generation_id: str = ""
    content: str = ""


class ListPolicyGenerationAssetsResponse(BaseModel):
    policy_generation_assets: list[PolicyGenerationAssetInfo] = Field(default_factory=list)
    next_token: str | None = None
