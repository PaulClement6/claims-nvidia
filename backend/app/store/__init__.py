"""SQLite store (stdlib, no ORM). Seeded from data/mock/policies.json."""

import json
import os
import sqlite3
import uuid
from pathlib import Path

from app.models import Claim, ClaimType, Customer, LineOfBusiness, Policy

SEED = Path(__file__).resolve().parents[3] / "data" / "mock" / "policies.json"
CLAIM_TYPE: dict[LineOfBusiness, ClaimType] = {
    "Travel": "travel",
    "Household": "property",
    "Accident": "health",
}


class Store:
    def __init__(self, path: str | None = None) -> None:
        path = path or os.environ.get("CLAIMS_DB", ".dev-data/claims.db")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS policies (
                number TEXT PRIMARY KEY, customer_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY, data TEXT NOT NULL);
            """
        )
        if not self.db.execute("SELECT 1 FROM customers").fetchone():
            self._seed()

    def _seed(self) -> None:
        seed = json.loads(SEED.read_text())
        with self.db:
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

    def create_claim(self, customer_id: str, policy_number: str) -> Claim | None:
        """Returns None when the policy is not one of this customer."""
        customer = next((c for c in self.get_customers() if c.id == customer_id), None)
        policy = next(
            (
                p
                for p in self.get_customer_policies(customer_id)
                if p.number == policy_number
            ),
            None,
        )
        if customer is None or policy is None:
            return None
        claim = Claim(
            id=f"CL-{uuid.uuid4().hex[:8]}",
            customer=customer,
            policy=policy,
            type=CLAIM_TYPE[policy.line_of_business],
        )
        with self.db:
            self.db.execute(
                "INSERT INTO claims VALUES (?, ?)", (claim.id, claim.model_dump_json())
            )
        return claim

    def get_claim(self, claim_id: str) -> Claim | None:
        row = self.db.execute(
            "SELECT data FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        return Claim.model_validate_json(row[0]) if row else None
