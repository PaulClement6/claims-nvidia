"""SQLite store (stdlib, no ORM). Seeded from data/mock/policies.json."""

import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.models import (
    Claim,
    ClaimType,
    Customer,
    Document,
    FraudReport,
    LineOfBusiness,
    Message,
    Outcome,
    PipelineEvent,
    Policy,
)

# CLAIMS_DATA overrides it (the Docker image does not hold the repository).
DATA = Path(__file__).resolve().parents[3] / "data"
CLAIM_TYPE: dict[LineOfBusiness, ClaimType] = {
    "Travel": "travel",
    "Household": "property",
    "Accident": "health",
}


class InvestigationWithoutReport(ValueError):
    """Non-negotiable 4: a claim routed to investigation always has a fraud report."""


class Store:
    def __init__(self, path: str | None = None) -> None:
        path = path or os.environ.get("CLAIMS_DB", ".dev-data/claims.db")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        # One connection for all threads: every write, the sequence allocation and
        # every read-modify-write of a claim run under this lock (reentrant: the
        # read-modify-write methods call _save_claim).
        self.lock = threading.RLock()
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS policies (
                number TEXT PRIMARY KEY, customer_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                claim_id TEXT NOT NULL, sequence INTEGER NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY (claim_id, sequence));
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL, data TEXT NOT NULL);
            """
        )
        if not self.db.execute("SELECT 1 FROM customers").fetchone():
            self._seed()

    def _seed(self) -> None:
        data = Path(os.environ.get("CLAIMS_DATA") or DATA)
        seed = json.loads((data / "mock" / "policies.json").read_text())
        with self.lock, self.db:
            for c in map(Customer.model_validate, seed["customers"]):
                self.db.execute(
                    "INSERT INTO customers VALUES (?, ?)", (c.id, c.model_dump_json())
                )
            for p in map(Policy.model_validate, seed["policies"]):
                self.db.execute(
                    "INSERT INTO policies VALUES (?, ?, ?)",
                    (p.number, p.customer_id, p.model_dump_json()),
                )

    def get_customers(self) -> list[Customer]:
        rows = self.db.execute("SELECT data FROM customers ORDER BY id")
        return [Customer.model_validate_json(r[0]) for r in rows]

    def get_customer_policies(self, customer_id: str) -> list[Policy]:
        rows = self.db.execute(
            "SELECT data FROM policies WHERE customer_id = ? ORDER BY number",
            (customer_id,),
        )
        return [Policy.model_validate_json(r[0]) for r in rows]

    def create_claim(self, customer_id: str) -> Claim | None:
        """An open claim with no policy yet. None when the customer is unknown."""
        customer = next((c for c in self.get_customers() if c.id == customer_id), None)
        if customer is None:
            return None
        claim = Claim(id=f"CL-{uuid.uuid4().hex[:8]}", customer=customer)
        self._save_claim(claim)
        return claim

    def set_claim_policy(self, claim_id: str, policy_number: str) -> Claim | None:
        """None when the claim is unknown or the policy is not this customer's."""
        with self.lock:
            claim = self.get_claim(claim_id)
            if claim is None:
                return None
            policy = next(
                (
                    p
                    for p in self.get_customer_policies(claim.customer.id)
                    if p.number == policy_number
                ),
                None,
            )
            if policy is None:
                return None
            claim.policy = policy
            claim.type = CLAIM_TYPE[policy.line_of_business]
            self._save_claim(claim)
            return claim

    def find_document(self, document_id: str) -> tuple[Claim, Document] | None:
        # ponytail: scans all claims, add a documents table if the demo grows
        with self.lock:
            rows = self.db.execute("SELECT data FROM claims").fetchall()
        for (data,) in rows:
            claim = Claim.model_validate_json(data)
            for doc in claim.documents:
                if doc.id == document_id:
                    return claim, doc
        return None

    def save_document(self, claim_id: str, document: Document) -> Claim | None:
        """Adds the document, or replaces the one with the same id."""
        with self.lock:
            claim = self.get_claim(claim_id)
            if claim is None:
                return None
            docs = [document if d.id == document.id else d for d in claim.documents]
            claim.documents = docs if document in docs else [*docs, document]
            self._save_claim(claim)
            return claim

    def update_claim(self, claim_id: str, fields: dict[str, Any]) -> Claim | None:
        with self.lock:
            claim = self.get_claim(claim_id)
            if claim is None:
                return None
            claim = Claim.model_validate({**claim.model_dump(mode="json"), **fields})
            self._save_claim(claim)
            return claim

    def add_message(
        self, claim_id: str, role: Literal["customer", "agent"], text: str
    ) -> Message:
        message = Message(role=role, text=text, time=datetime.now(UTC))
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO messages (claim_id, data) VALUES (?, ?)",
                (claim_id, message.model_dump_json()),
            )
        return message

    def get_messages(self, claim_id: str) -> list[Message]:
        with self.lock:  # a read next to another thread's write sees half of it
            rows = self.db.execute(
                "SELECT data FROM messages WHERE claim_id = ? ORDER BY id", (claim_id,)
            ).fetchall()
        return [Message.model_validate_json(r[0]) for r in rows]

    def save_routing(
        self,
        claim_id: str,
        score: int,
        outcome: Outcome,
        reasons: list[str],
        status: str,
        report: FraudReport | None = None,
    ) -> Claim | None:
        """Outcome, score, reasons and report in one write: an investigation and
        its report are saved together or not at all."""
        fields = {
            "score": score,
            "outcome": outcome.value,
            "outcome_reasons": reasons,
            "status": status,
            "fraud_report": report.model_dump(mode="json") if report else None,
        }
        return self.update_claim(claim_id, fields)

    def _save_claim(self, claim: Claim) -> None:
        """Every save of a claim goes through here: the one place of rule 4."""
        if claim.outcome == Outcome.investigation and claim.fraud_report is None:
            raise InvestigationWithoutReport(
                "a claim with outcome investigation needs a fraud report"
            )
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO claims VALUES (?, ?)",
                (claim.id, claim.model_dump_json()),
            )

    def append_event(
        self,
        claim_id: str,
        actor: Literal["agent", "tool", "workflow", "sandbox"],
        stage: str,
        title: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        model: str | None = None,
        duration: float | None = None,
    ) -> PipelineEvent:
        with self.lock, self.db:
            (sequence,) = self.db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE claim_id = ?",
                (claim_id,),
            ).fetchone()
            event = PipelineEvent(
                sequence=sequence,
                time=datetime.now(UTC),
                actor=actor,
                stage=stage,
                title=title,
                inputs=inputs or {},
                outputs=outputs or {},
                model=model,
                duration=duration,
            )
            self.db.execute(
                "INSERT INTO events VALUES (?, ?, ?)",
                (claim_id, sequence, event.model_dump_json()),
            )
        return event

    def events_since(self, claim_id: str, sequence: int) -> list[PipelineEvent]:
        with self.lock:
            rows = self.db.execute(
                "SELECT data FROM events WHERE claim_id = ? AND sequence > ? "
                "ORDER BY sequence",
                (claim_id, sequence),
            ).fetchall()
        return [PipelineEvent.model_validate_json(r[0]) for r in rows]

    def get_claim(self, claim_id: str) -> Claim | None:
        with self.lock:
            row = self.db.execute(
                "SELECT data FROM claims WHERE id = ?", (claim_id,)
            ).fetchone()
        return Claim.model_validate_json(row[0]) if row else None
