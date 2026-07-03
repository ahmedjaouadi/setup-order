from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnalyzeRequestModel(BaseModel):
    symbol: str = ""
    text: str = ""
    payload: dict[str, Any] | list[Any] | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    force_new_revision: bool = False
    ambiguities: list[dict[str, Any]] = Field(default_factory=list)


class ResolveAmbiguityRequestModel(BaseModel):
    selected_option: dict[str, Any] | None = None
    field_value: Any = None
    comment: str | None = None


class CompareAnalysesRequestModel(BaseModel):
    left_analysis_id: str
    right_analysis_id: str
    left_scenario_id: str | None = None
    right_scenario_id: str | None = None


class RollbackAnalysisRequestModel(BaseModel):
    analysis_id: str
    scenario_id: str | None = None
