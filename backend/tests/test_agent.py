"""The scripted agent, driven through the HTTP API with claim file 1."""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.reader_contract import MOCK

CLAIM1 = MOCK / "claim-1-travel"
PDFS = ["booking-confirmation", "cancellation-confirmation", "medical-certificate"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def start(client: TestClient) -> str:
    cid = str(client.post("/claims", json={"customer_id": "C-1001"}).json()["id"])
    res = client.put(f"/claims/{cid}/policy", json={"policy_number": "TRV-2026-0417"})
    assert res.status_code == 200
    return cid


def say(client: TestClient, cid: str, text: str) -> str:
    res = client.post(f"/claims/{cid}/messages", json={"text": text})
    assert res.status_code == 200
    return " ".join(m["text"] for m in res.json())


def upload(client: TestClient, cid: str, data: bytes, name: str = "a.pdf"):  # type: ignore[no-untyped-def]
    return client.post(f"/claims/{cid}/documents", files={"file": (name, data)})


def state(client: TestClient, cid: str) -> str:
    return str(client.get(f"/claims/{cid}").json()["agent_state"])


def test_policy_gives_a_greeting_that_names_the_policy(client: TestClient) -> None:
    cid = start(client)
    [m] = client.get(f"/claims/{cid}/messages").json()
    assert m["role"] == "agent" and "TRV-2026-0417" in m["text"]
    assert "What happened" in m["text"] and m["time"]
    assert state(client, cid) == "ask_story"


def test_claim_file_1_ends_closed_with_the_result(client: TestClient) -> None:
    cid = start(client)
    assert "date" in say(client, cid, "My trip was cancelled because I was ill.")
    assert state(client, cid) == "ask_date"
    assert "amount" in say(client, cid, "3 August 2026")
    assert state(client, cid) == "ask_amount"
    assert "booking confirmation" in say(client, cid, "CHF 1'500.00")
    assert state(client, cid) == "ask_documents"

    for i, name in enumerate(PDFS):
        res = upload(client, cid, (CLAIM1 / f"{name}.pdf").read_bytes(), f"{name}.pdf")
        assert res.status_code == 201
        last = client.get(f"/claims/{cid}/messages").json()
        assert last[-2] == {
            **last[-2],
            "role": "customer",
            "text": f"Uploaded {name}.pdf",
        }
        assert last[-1]["role"] == "agent"
        if i < 2:
            assert state(client, cid) == "ask_documents"
            assert "still" in last[-1]["text"].lower()
        else:
            assert "validated" in last[-1]["text"]

    claim = client.get(f"/claims/{cid}").json()
    assert claim["agent_state"] == "closed"
    assert "validated" in last[-1]["text"]
    assert claim["cause"] == "illness"
    assert claim["incident_date"] == "2026-08-03" and claim["claimed_amount"] == 1500
    assert [d["document_type"] for d in claim["documents"]] == [
        "booking_confirmation",
        "cancellation_confirmation",
        "medical_certificate",
    ]
    assert all(d["fields"] for d in claim["documents"])
    for word in ("model", "tool", "score", "fraud", "check"):
        assert word not in " ".join(m["text"] for m in last).lower().replace(
            "checks", ""
        )


def test_one_message_fills_three_slots(client: TestClient) -> None:
    cid = start(client)
    reply = say(
        client,
        cid,
        "My trip was cancelled on 3 August 2026 because I was ill, I claim CHF 1,500",
    )
    assert "booking confirmation" in reply
    claim = client.get(f"/claims/{cid}").json()
    assert claim["agent_state"] == "ask_documents"
    assert claim["incident_date"] == "2026-08-03" and claim["claimed_amount"] == 1500
    assert claim["cause"] == "illness" and "cancelled" in claim["description"]


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("2026-08-03", "2026-08-03"),
        ("03.08.2026", "2026-08-03"),
        ("3 August 2026", "2026-08-03"),
        ("August 3, 2026", "2026-08-03"),
        ("on the 3rd of August 2026", "2026-08-03"),
    ],
)
def test_date_formats(client: TestClient, text: str, iso: str) -> None:
    cid = start(client)
    say(client, cid, "My luggage was lost.")
    say(client, cid, text)
    claim = client.get(f"/claims/{cid}").json()
    assert claim["incident_date"] == iso and claim["cause"] == "luggage"


@pytest.mark.parametrize(
    ("text", "amount"),
    [("1500", 1500), ("1,500", 1500), ("1'500.00", 1500), ("CHF 1500", 1500)],
)
def test_amount_formats(client: TestClient, text: str, amount: float) -> None:
    cid = start(client)
    say(client, cid, "The hotel was cancelled.")
    say(client, cid, "2026-08-03")
    say(client, cid, text)
    claim = client.get(f"/claims/{cid}").json()
    assert claim["claimed_amount"] == amount and claim["cause"] == "cancellation"


def test_unreadable_date_and_amount_are_asked_again_with_an_example(
    client: TestClient,
) -> None:
    cid = start(client)
    say(client, cid, "The trip was cancelled.")
    assert "3 August 2026" in say(client, cid, "last week")
    assert state(client, cid) == "ask_date"
    assert "3 August 2026" in say(client, cid, "31.02.2026")
    assert state(client, cid) == "ask_date"
    say(client, cid, "3 August 2026")
    assert "CHF 1,500" in say(client, cid, "a lot")
    assert state(client, cid) == "ask_amount"


def test_unknown_document_is_not_counted(client: TestClient) -> None:
    cid = start(client)
    say(client, cid, "The trip was cancelled on 3 August 2026, I claim CHF 900")
    other = (
        (CLAIM1 / "booking-confirmation.pdf")
        .read_bytes()
        .replace(b"Booking confirmation", b"Something else!!!!!!")
    )
    assert upload(client, cid, other).status_code == 201
    last = client.get(f"/claims/{cid}/messages").json()[-1]["text"]
    assert "could not recognise" in last and "booking confirmation" in last
    assert state(client, cid) == "ask_documents"


def test_done_goes_on_with_documents_missing(client: TestClient) -> None:
    cid = start(client)
    say(client, cid, "The trip was cancelled on 3 August 2026, I claim CHF 900")
    reply = say(client, cid, "Done.")
    assert state(client, cid) == "closed"
    assert "claims specialist will review your case" in reply


def test_messages_errors(client: TestClient) -> None:
    cid = start(client)
    assert (
        client.post("/claims/CL-nope/messages", json={"text": "hi"}).status_code == 404
    )
    assert client.get("/claims/CL-nope/messages").status_code == 404
    assert (
        client.post(f"/claims/{cid}/messages", json={"text": "  "}).status_code == 422
    )
    bare = client.post("/claims", json={"customer_id": "C-1001"}).json()["id"]
    assert (
        client.post(f"/claims/{bare}/messages", json={"text": "hi"}).status_code == 409
    )


def test_each_agent_message_is_an_agent_event(client: TestClient) -> None:
    cid = start(client)
    say(client, cid, "The trip was cancelled.")
    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    stored = client.get(f"/claims/{cid}/messages").json()
    agent_texts = [m["text"] for m in stored if m["role"] == "agent"]
    msgs = [e for e in events if e.actor == "agent"]
    assert [e.stage for e in msgs] == ["message"] * 2
    assert [e.title for e in msgs] == [t[:80] for t in agent_texts]


def test_agent_does_not_import_the_store() -> None:
    import app.agent.scripted as scripted

    tree = ast.parse(Path(scripted.__file__).read_text())
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module)
        elif isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
    assert not any(str(n).startswith("app.store") for n in names), names
    assert "app.tools.gateway" in names


def test_every_data_access_is_a_gateway_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    calls: list[str] = []
    real = gateway.call

    def spy(claim_id: str, tool: str, inputs: dict[str, object]) -> dict[str, object]:
        calls.append(tool)
        out: dict[str, object] = real(claim_id, tool, inputs)
        return out

    monkeypatch.setattr(gateway, "call", spy)
    cid = start(client)
    say(client, cid, "The trip was cancelled on 3 August 2026, I claim CHF 900")
    upload(client, cid, (CLAIM1 / "booking-confirmation.pdf").read_bytes())
    say(client, cid, "done")

    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    assert [e.stage for e in events if e.actor == "tool"] == calls
    assert {"get_claim_summary", "update_claim", "required_documents"} <= set(calls)
    assert {"classify_document", "extract_document"} <= set(calls)
    assert calls[-4:] == [
        "update_claim",  # ready_for_checks
        "run_checks",
        "assess_and_route",
        "update_claim",  # closed
    ]
