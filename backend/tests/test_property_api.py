"""Claim file 2 and its variants (property), end to end through the HTTP API."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.reader_contract import MOCK

CLAIM2 = MOCK / "claim-2-property"
FILES = ["damage-photo-1.jpg", "damage-photo-2.jpg", "repair-invoice.pdf"]
STORY = "Water damage in the kitchen."
REVIEW = "A claims specialist will review your case and contact you."


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def run_claim(
    client: TestClient,
    files: list[str] = FILES,
    story: str = STORY,
    amount: str = "CHF 3,200",
    done: bool = False,
) -> str:
    cid = str(client.post("/claims", json={"customer_id": "C-1002"}).json()["claim_id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "HH-2024-0933"})
    for text in (story, "14 September 2026", amount):
        assert (
            client.post(f"/claims/{cid}/messages", json={"text": text}).status_code
            == 200
        )
    for name in files:
        data = (CLAIM2 / name).read_bytes()
        res = client.post(f"/claims/{cid}/documents", files={"file": (name, data)})
        assert res.status_code == 201
    if done:
        client.post(f"/claims/{cid}/messages", json={"text": "done"})
    return cid


def claim(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}").json()
    return out


def test_claim_file_2_gives_no_signal_score_0_and_handler_review(
    client: TestClient,
) -> None:
    cid = run_claim(client)
    c = claim(client, cid)
    assert c["type"] == "property" and c["cause"] == "water"
    assert c["signals"] == [] and c["score"] == 0
    assert c["outcome"] == "handler_review"
    assert c["outcome_reasons"] == [
        "Claimed amount CHF 3,200.00 is above the straight-through limit of CHF 2,000."
    ]
    assert c["agent_state"] == "closed" and c["status"] == "in_review"
    last = client.get(f"/claims/{cid}/messages").json()[-1]
    assert last["role"] == "agent" and REVIEW in last["text"]
    assert client.get(f"/claims/{cid}/customer").json()["message"] == REVIEW


def test_claim_file_2_documents_are_read_with_sources(client: TestClient) -> None:
    c = claim(client, run_claim(client))
    kinds = {d["file_metadata"]["original_name"]: d for d in c["documents"]}
    invoice = kinds["repair-invoice.pdf"]
    assert invoice["document_type"] == "repair_invoice"
    assert len(invoice["line_items"]) == 4
    assert sum(i["amount"] for i in invoice["line_items"]) == 3200.0
    assert {f["name"] for f in invoice["fields"]} >= {"invoice_date", "total"}
    for name in ("damage-photo-1.jpg", "damage-photo-2.jpg"):
        photo = kinds[name]
        assert photo["document_type"] == "damage_photo" and photo["pages"] == 1
        [f] = photo["fields"]
        assert f["name"] == "damage_description" and f["source"]["page"] == 1
        assert (
            f["source"]["box"] is not None and f["source"]["document_id"] == photo["id"]
        )


def test_the_agent_says_what_it_read(client: TestClient) -> None:
    cid = run_claim(client)
    text = " ".join(m["text"] for m in client.get(f"/claims/{cid}/messages").json())
    assert "I read this as a damage photo." in text
    assert "I read this as a repair invoice." in text


def test_a_photo_alone_leaves_the_invoice_missing(client: TestClient) -> None:
    cid = run_claim(client, ["damage-photo-1.jpg"])
    assert claim(client, cid)["agent_state"] == "ask_documents"
    text = client.get(f"/claims/{cid}/messages").json()[-1]["text"]
    assert "a repair invoice or a repair quote" in text


def test_theft_with_no_police_report_gives_r001(client: TestClient) -> None:
    cid = run_claim(client, story="My bike was stolen from the cellar.", done=True)
    c = claim(client, cid)
    assert c["cause"] == "theft"
    [s] = c["signals"]
    assert s["code"] == "R001" and "a police report" in s["detail"]
    assert c["score"] == 25 and c["outcome"] == "handler_review"
    assert c["status"] == "in_review"


def test_a_water_claim_needs_no_police_report(client: TestClient) -> None:
    cid = run_claim(client, amount="CHF 1,900")
    c = claim(client, cid)
    assert c["signals"] == [] and c["outcome"] == "straight_through"
    assert c["status"] == "validated"


@pytest.mark.parametrize(
    ("story", "cause"),
    [
        ("A pipe leaked", "water"),
        ("The flood came through the door", "water"),
        ("Fire in the garage", "fire"),
        ("There was smoke everywhere", "fire"),
        ("A burglary last night", "theft"),
        ("The window is cracked", "breakage"),
        ("It broke when I moved it", "breakage"),
        ("Something strange happened", "other"),
    ],
)
def test_cause_from_the_story(client: TestClient, story: str, cause: str) -> None:
    cid = str(client.post("/claims", json={"customer_id": "C-1002"}).json()["claim_id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "HH-2024-0933"})
    client.post(f"/claims/{cid}/messages", json={"text": story})
    assert claim(client, cid)["cause"] == cause


def test_the_file_of_a_document_is_streamed_with_its_content_type(
    client: TestClient,
) -> None:
    cid = run_claim(client)
    for d in claim(client, cid)["documents"]:
        res = client.get(f"/claims/{cid}/documents/{d['id']}/file")
        name = d["file_metadata"]["original_name"]
        assert res.status_code == 200 and res.content == (CLAIM2 / name).read_bytes()
        want = "application/pdf" if name.endswith(".pdf") else "image/jpeg"
        assert res.headers["content-type"] == want
    assert client.get(f"/claims/{cid}/documents/D-nope/file").status_code == 404
    assert client.get("/claims/CL-nope/documents/D-x/file").status_code == 404


def test_a_document_of_another_claim_is_not_served(client: TestClient) -> None:
    first = run_claim(client)
    second = str(
        client.post("/claims", json={"customer_id": "C-1002"}).json()["claim_id"]
    )
    doc_id = claim(client, first)["documents"][0]["id"]
    assert client.get(f"/claims/{second}/documents/{doc_id}/file").status_code == 404
