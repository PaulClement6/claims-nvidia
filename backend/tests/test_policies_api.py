from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Outcome
from app.store import Store


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


def test_customers(client: TestClient) -> None:
    customers = client.get("/customers").json()
    assert [c["id"] for c in customers] == ["C-1001", "C-1002", "C-1003"]


def new_claim(client: TestClient, customer: str = "C-1001") -> dict[str, object]:
    res = client.post("/claims", json={"customer_id": customer})
    assert res.status_code == 201
    claim: dict[str, object] = res.json()
    return claim


def test_new_claim_is_open_with_no_policy_and_no_type(client: TestClient) -> None:
    claim = new_claim(client)
    assert claim["policy"] is None
    assert claim["type"] is None
    assert claim["status"] == "open"
    assert client.get(f"/claims/{claim['id']}").json() == claim


def test_claim_for_unknown_customer_is_404(client: TestClient) -> None:
    res = client.post("/claims", json={"customer_id": "C-9999"})
    assert res.status_code == 404


def test_six_policies_through_the_gateway_with_one_event_each(
    client: TestClient,
) -> None:
    numbers: list[str] = []
    for customer in ["C-1001", "C-1002", "C-1003"]:
        claim = new_claim(client, customer)
        res = client.get(f"/claims/{claim['id']}/policies")
        assert res.status_code == 200
        numbers += [p["number"] for p in res.json()]
        store: Store = client.app.state.store  # type: ignore[attr-defined]
        [event] = store.events_since(str(claim["id"]), 0)
        assert event.stage == "get_customer_policies"
        assert event.inputs == {"customer_id": customer}
        assert len(event.outputs["policies"]) == len(res.json())
    assert len(numbers) == 6
    assert "TRV-2026-0417" in numbers


def test_policies_of_an_unknown_claim_is_404(client: TestClient) -> None:
    assert client.get("/claims/nope/policies").status_code == 404


@pytest.mark.parametrize(
    ("customer", "policy", "claim_type"),
    [
        ("C-1001", "TRV-2026-0417", "travel"),
        ("C-1002", "HH-2024-0933", "property"),
        ("C-1003", "ACC-2026-2210", "health"),
    ],
)
def test_set_policy_gives_type_from_line(
    client: TestClient, customer: str, policy: str, claim_type: str
) -> None:
    claim = new_claim(client, customer)
    res = client.put(f"/claims/{claim['id']}/policy", json={"policy_number": policy})
    assert res.status_code == 200
    body = res.json()
    assert body["type"] == claim_type
    assert body["policy"]["number"] == policy
    assert client.get(f"/claims/{claim['id']}").json() == body


def test_policy_of_another_customer_is_404(client: TestClient) -> None:
    claim = new_claim(client, "C-1002")
    res = client.put(
        f"/claims/{claim['id']}/policy", json={"policy_number": "TRV-2026-0417"}
    )
    assert res.status_code == 404
    assert client.get(f"/claims/{claim['id']}").json()["policy"] is None


def test_set_policy_of_an_unknown_claim_is_404(client: TestClient) -> None:
    res = client.put("/claims/nope/policy", json={"policy_number": "TRV-2026-0417"})
    assert res.status_code == 404


def test_unknown_claim_is_404(client: TestClient) -> None:
    assert client.get("/claims/nope").status_code == 404
