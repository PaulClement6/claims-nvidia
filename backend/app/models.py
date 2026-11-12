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


class DocumentType(StrEnum):
    booking_confirmation = "booking_confirmation"
    cancellation_confirmation = "cancellation_confirmation"
    medical_certificate = "medical_certificate"
    repair_invoice = "repair_invoice"
    repair_quote = "repair_quote"
    damage_photo = "damage_photo"
    police_report = "police_report"
    medical_invoice = "medical_invoice"
    medical_report = "medical_report"
    receipt = "receipt"
    unknown = "unknown"


class Document(BaseModel):
    id: str
    file: str
    document_type: str = "unknown"
    pages: int = Field(default=1, ge=1)
    fields: list[ExtractedField] = []
    line_items: list[dict[str, Any]] = []
    file_metadata: dict[str, Any] = {}


class Extraction(BaseModel):
    """What a Reader returns for one document (the contract of extract_document)."""

    document_type: DocumentType
    fields: list[ExtractedField] = []
    line_items: list[dict[str, Any]] = []


class Signal(BaseModel):
    code: str
    category: Literal["rule", "tampering", "consistency"]
    severity: Literal["low", "medium", "high"]
    points: int
    title: str
    detail: str  # what the system saw
    expected: str = ""  # what it expected: the same text for every signal of a code
    evidence: list[Source] = []


class FraudFinding(BaseModel):
    """One signal in the report: what was seen, where, and what was expected."""

    code: str
    title: str
    saw: str
    where: list[str]  # "file name, page N"; a plain note when no document holds it
    expected: str


class FraudReport(BaseModel):
    """Written for every `investigation` (docs/FRAUD.md), from the signals only."""

    outcome: Outcome
    score: int
    summary: str  # one plain sentence that says why
    findings: list[FraudFinding] = []
    not_checked: list[str] = []
    next_steps: list[str] = []


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
    cause: str | None = None  # travel: illness, luggage or cancellation
    agent_state: str = "ask_story"
    documents: list[Document] = []
    signals: list[Signal] = []
    score: int | None = None
    outcome: Outcome | None = None
    outcome_reasons: list[str] = []
    fraud_report: FraudReport | None = None
    status: str = "open"


class Message(BaseModel):
    role: Literal["customer", "agent"]
    text: str
    time: datetime
