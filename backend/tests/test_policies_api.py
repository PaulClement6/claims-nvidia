from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Outcome


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    with TestClient(create_app()) as c:
        yield c


def test_outcome_has_exactly_three_values() -> None:
    assert {o.value for o in Outcome} == {
        "straight_through",
        "handler_review",
        "investigation",
    }


def test_customers_and_six_policies(client: TestClient) -> None:
    customers = client.get("/customers").json()
    assert [c["id"] for c in customers] == ["C-1001", "C-1002", "C-1003"]
    numbers = [
        p["number"]
        for c in customers
        for p in client.get(f"/customers/{c['id']}/policies").json()
    ]
    assert len(numbers) == 6
    assert "TRV-2026-0417" in numbers


def test_unknown_customer_is_404(client: TestClient) -> None:
    assert client.get("/customers/C-9999/policies").status_code == 404


@pytest.mark.parametrize(
    ("customer", "policy", "claim_type"),
    [
        ("C-1001", "TRV-2026-0417", "travel"),
        ("C-1002", "HH-2024-0933", "property"),
        ("C-1003", "ACC-2026-2210", "health"),
    ],
)
def test_create_claim_type_from_line(
    client: TestClient, customer: str, policy: str, claim_type: str
) -> None:
    res = client.post(
        "/claims", json={"customer_id": customer, "policy_number": policy}
    )
    assert res.status_code == 201
    claim = res.json()
    assert claim["type"] == claim_type
    assert client.get(f"/claims/{claim['id']}").json() == claim


def test_claim_on_policy_of_another_customer_is_404(client: TestClient) -> None:
    res = client.post(
        "/claims", json={"customer_id": "C-1002", "policy_number": "TRV-2026-0417"}
    )
    assert res.status_code == 404


def test_unknown_claim_is_404(client: TestClient) -> None:
    assert client.get("/claims/nope").status_code == 404
