"""Typed shapes shared by the agent, the API and the scripts."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    """One memory returned by recall (a fact, an experience or a consolidated observation)."""

    ref: str = Field(description="Short handle shown to the LLM, e.g. M3")
    id: str
    text: str
    type: Optional[str] = None  # world | experience | observation
    occurred: Optional[str] = None
    case_id: Optional[str] = None
    root_cause: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    score: Optional[float] = None


class Evidence(BaseModel):
    memory_ref: str
    why_relevant: str
    case_id: Optional[str] = None
    text: Optional[str] = None
    occurred: Optional[str] = None
    type: Optional[str] = None


class Diagnosis(BaseModel):
    case_id: str
    used_memory: bool
    root_cause: str
    root_cause_label: str
    confidence: float
    reasoning: str
    recommended_action: str
    draft_message: str
    prevention_tip: str
    evidence: list[Evidence] = Field(default_factory=list)
    recalled: list[MemoryItem] = Field(default_factory=list)
    requires_human_approval: bool = False
    auto_fix_eligible: bool = False
    guardrail_notes: list[str] = Field(default_factory=list)
    model: str = ""
    latency_ms: int = 0
    degraded: bool = False  # true when we had to fall back (model error, memory down, ...)
    warnings: list[str] = Field(default_factory=list)


class DiagnoseRequest(BaseModel):
    use_memory: bool = True


class ResolveRequest(BaseModel):
    root_cause: str
    note: str = Field(min_length=3, max_length=2000)
    agent_root_cause: Optional[str] = None
    agent_confidence: Optional[float] = None
    resolved_by: str = "analyst"


class ResolveResult(BaseModel):
    case_id: str
    root_cause: str
    agent_was_right: Optional[bool]
    retained: bool
    queued_for_retry: bool = False
    message: str


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class AskResult(BaseModel):
    answer: str
    memories: list[dict[str, Any]] = Field(default_factory=list)
    mental_models: list[dict[str, Any]] = Field(default_factory=list)
    directives: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: int = 0


class PrecheckRequest(BaseModel):
    client: str
    beneficiary: str
    beneficiary_bank: str
    currency: str
    amount: float
    submit_time_sgt: str = Field(default="10:00", description="HH:MM, Singapore time")
    submit_date: Optional[str] = None
    intermediary: Optional[str] = None
    beneficiary_account: Optional[str] = None
    remittance_info: Optional[str] = None


class SimSendRequest(PrecheckRequest):
    """A payment sent to the network simulator (same fields as a pre-flight check)."""

    beneficiary_account: str = Field(min_length=4, max_length=40)
    payment_ref: Optional[str] = Field(default=None, max_length=20)


class SimSendResult(BaseModel):
    payment_ref: str
    status: Literal["credited", "credited_next_day", "rejected", "held"]
    iso_status: str
    bank: str
    bic: str = ""
    message: str
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None
    case_id: Optional[str] = None


class PrecheckWarning(BaseModel):
    title: str
    detail: str
    severity: Literal["low", "medium", "high"] = "medium"
    fix: str = ""


class PrecheckResult(BaseModel):
    risk_level: Literal["low", "medium", "high"]
    summary: str
    warnings: list[PrecheckWarning] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    directives: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: int = 0
