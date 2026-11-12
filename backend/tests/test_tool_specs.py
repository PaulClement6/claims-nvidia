"""The tools the LLM agent sees (issue #12): JSON schemas, set at registration."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store
from app.tools.gateway import Gateway
from tests.reader_contract import MOCK

EXPOSED = {
    "get_claim_summary",
    "update_claim",
    "required_documents",
    "classify_document",
    "extract_document",
    "run_checks",
    "check_tampering",
    "check_consistency",
    "assess_and_route",
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def test_a_tool_with_a_schema_is_listed_in_the_openai_format(tmp_path: Path) -> None:
    gateway = Gateway(Store(str(tmp_path / "c.db")))
    gateway.register("hidden", lambda: {})
    gateway.register(
        "add",
        lambda a: {"a": a},
        description="Add.",
        parameters={"type": "object", "properties": {"a": {"type": "number"}}},
    )
    assert gateway.tool_specs() == [
        {
            "type": "function",
            "function": {
                "name": "add",
                "description": "Add.",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "number"}},
                },
            },
        }
    ]


def test_the_model_sees_the_agent_tools_and_never_the_claim_id_or_the_state(
    client: TestClient,
) -> None:
    specs = client.app.state.gateway.tool_specs()  # type: ignore[attr-defined]
    assert {s["function"]["name"] for s in specs} == EXPOSED
    for s in specs:
        params = s["function"]["parameters"]
        assert params["type"] == "object" and s["function"]["description"]
        assert "claim_id" not in params["properties"]
        assert "agent_state" not in params["properties"]
        assert params.get("additionalProperties") is False


def test_the_summary_lists_the_documents_and_the_causes_of_the_claim_type(
    client: TestClient,
) -> None:
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    cid = client.post("/claims", json={"customer_id": "C-1001"}).json()["claim_id"]
    client.put(f"/claims/{cid}/policy", json={"policy_number": "TRV-2026-0417"})
    data = (MOCK / "claim-1-travel" / "booking-confirmation.pdf").read_bytes()
    doc = client.post(
        f"/claims/{cid}/documents", files={"file": ("booking.pdf", data)}
    ).json()
    s = gateway.call(cid, "get_claim_summary", {"claim_id": cid})
    assert s["allowed_causes"] == ["illness", "luggage", "cancellation"]
    assert s["documents"] == [
        {"id": doc["id"], "file_name": "booking.pdf", "type": "booking_confirmation"}
    ]


def test_update_claim_refuses_a_cause_the_claim_type_does_not_know(
    client: TestClient,
) -> None:
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    cid = client.post("/claims", json={"customer_id": "C-1001"}).json()["claim_id"]
    client.put(f"/claims/{cid}/policy", json={"policy_number": "TRV-2026-0417"})
    with pytest.raises(ValueError, match="illness, luggage, cancellation"):
        gateway.call(cid, "update_claim", {"claim_id": cid, "cause": "flood"})
    out: dict[str, Any] = gateway.call(
        cid, "update_claim", {"claim_id": cid, "cause": "illness"}
    )
    assert out == {"updated": ["cause"]}


def test_a_document_of_another_claim_cannot_be_read(client: TestClient) -> None:
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    ids = []
    for customer, policy in (("C-1001", "TRV-2026-0417"), ("C-1002", "HH-2024-0933")):
        cid = client.post("/claims", json={"customer_id": customer}).json()["claim_id"]
        client.put(f"/claims/{cid}/policy", json={"policy_number": policy})
        ids.append(cid)
    data = (MOCK / "claim-1-travel" / "booking-confirmation.pdf").read_bytes()
    doc = client.post(
        f"/claims/{ids[0]}/documents", files={"file": ("b.pdf", data)}
    ).json()
    for tool in ("classify_document", "extract_document"):
        with pytest.raises(KeyError, match="unknown document"):
            gateway.call(ids[1], tool, {"claim_id": ids[1], "document_id": doc["id"]})
        ok = gateway.call(ids[0], tool, {"claim_id": ids[0], "document_id": doc["id"]})
        assert ok["document_type"] == "booking_confirmation"
    # the scripted agent calls with the document id alone: still works
    assert gateway.call(ids[0], "classify_document", {"document_id": doc["id"]})
