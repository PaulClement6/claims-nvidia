"""Pydantic models of docs/ARCHITECTURE.md section 4 (later tickets import them)."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

LineOfBusiness = Literal["Travel", "Household", "Accident"]
ClaimType = Literal["travel", "property", "health"]


class Outcome(StrEnum):
    """No rejection (non-negotiable 1): only these three outcomes exist."""

    straight_through = "straight_through"
    handler_review = "handler_review"
    investigation = "investigation"


class Customer(BaseModel):
    id: str
    name: str


class Policy(BaseModel):
    number: str
    customer_id: str
    line_of_business: LineOfBusiness
    start: date
    end: date
    coverage_limit: float


class Source(BaseModel):
    """Where a value was read: document, page (1 or more), snippet or box."""

    document_id: str
    page: int = Field(ge=1)
    snippet: str | None = None
    box: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1

    @model_validator(mode="after")
    def _snippet_or_box(self) -> Self:
        if not self.snippet and self.box is None:
            raise ValueError("a source needs a snippet or a box")
        return self


class ExtractedField(BaseModel):
    """Non-negotiable 3: `source` is required, a field with no source is refused."""

    name: str
    value: Any
    confidence: float = Field(ge=0, le=1)
    source: Source


class Document(BaseModel):
    id: str
    file: str
    document_type: str = "unknown"
    pages: int = Field(default=1, ge=1)
    fields: list[ExtractedField] = []
    line_items: list[dict[str, Any]] = []
    file_metadata: dict[str, Any] = {}


class Signal(BaseModel):
    code: str
    category: Literal["rule", "tampering", "consistency"]
    severity: Literal["low", "medium", "high"]
    points: int
    title: str
    detail: str
    evidence: list[Source] = []


class FraudReport(BaseModel):
    summary: str
    signals: list[Signal]


class PipelineEvent(BaseModel):
    sequence: int
    time: datetime
    actor: Literal["agent", "tool", "workflow", "sandbox"]
    stage: str
    title: str
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    model: str | None = None
    duration: float | None = None  # seconds


class Claim(BaseModel):
    id: str
    customer: Customer
    policy: Policy | None = None
    type: ClaimType | None = None
    description: str | None = None
    incident_date: date | None = None
    claimed_amount: float | None = None
    documents: list[Document] = []
    signals: list[Signal] = []
    score: int | None = None
    outcome: Outcome | None = None
    fraud_report: FraudReport | None = None
    status: str = "open"
