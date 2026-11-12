"""The document kit of data/mock/index.json, played end to end in `local` mode.

Every story of every scenario is typed to the scripted agent through the HTTP API,
the documents are uploaded, the customer says "done", and the exact set of signal
codes and the outcome are checked. The three first claim files are scenarios of the
index too. A second test reads every document of the kit (the hard versions too)
through the reader contract.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.docintel.fixture import FixtureReader
from app.main import create_app
from app.models import Document, DocumentType
from tests.reader_contract import MOCK, check_reader_contract

INDEX: dict[str, Any] = json.loads((MOCK / "index.json").read_text())
SCENARIOS: list[dict[str, Any]] = INDEX["scenarios"]
STORIES = [(s, t) for s in SCENARIOS for t in s["stories"]]
IDS = [f"{s['folder']}-{t['outcome']}-{t['amount']:g}" for s, t in STORIES]
OUTCOMES = {"straight_through", "handler_review", "investigation"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("CLAIMS_MODE", raising=False)  # `local`
    with TestClient(create_app()) as c:
        yield c


@pytest.mark.parametrize(("scenario", "story"), STORIES, ids=IDS)
def test_story_gives_the_expected_signals_and_outcome(
    client: TestClient, scenario: dict[str, Any], story: dict[str, Any]
) -> None:
    cid = client.post("/claims", json={"customer_id": scenario["customer_id"]}).json()[
        "claim_id"
    ]
    res = client.put(
        f"/claims/{cid}/policy", json={"policy_number": scenario["policy"]}
    )
    assert res.status_code == 200
    # one message holds the story, the date and the amount
    client.post(f"/claims/{cid}/messages", json={"text": story["text"]})
    c = client.get(f"/claims/{cid}").json()
    assert c["agent_state"] == "ask_documents", "the agent did not read the story"
    assert c["incident_date"] == story["incident_date"]
    assert c["claimed_amount"] == story["amount"] and c["cause"] == story["cause"]

    for doc in scenario["documents"]:
        data = (MOCK / scenario["folder"] / doc["file"]).read_bytes()
        res = client.post(
            f"/claims/{cid}/documents", files={"file": (doc["file"], data)}
        )
        assert res.status_code == 201
    client.post(f"/claims/{cid}/messages", json={"text": "done"})

    c = client.get(f"/claims/{cid}").json()
    assert sorted(s["code"] for s in c["signals"]) == sorted(story["signals"])
    assert c["outcome"] == story["outcome"] and c["outcome"] in OUTCOMES
    assert c["agent_state"] == "closed"
    assert (c["fraud_report"] is not None) == (story["outcome"] == "investigation")
    kinds = sorted(d["document_type"] for d in c["documents"])
    assert kinds == sorted(d["document_type"] for d in scenario["documents"])


def test_the_scenarios_cover_the_three_outcomes_and_the_six_policies() -> None:
    assert {t["outcome"] for _, t in STORIES} == OUTCOMES
    policies = json.loads((MOCK / "policies.json").read_text())["policies"]
    assert {s["policy"] for s in SCENARIOS} == {p["number"] for p in policies}
    for s in SCENARIOS:  # the policy belongs to the customer of the scenario
        owner = next(p for p in policies if p["number"] == s["policy"])["customer_id"]
        assert owner == s["customer_id"], s["folder"]


def kit_files() -> set[str]:
    return {
        str(p.relative_to(MOCK))
        for p in MOCK.glob("*/*")
        if p.suffix in {".pdf", ".jpg", ".png"}
    }


def test_the_index_lists_every_document_of_the_kit_and_nothing_else() -> None:
    listed = {f"{s['folder']}/{d['file']}" for s in SCENARIOS for d in s["documents"]}
    listed |= {h["file"] for h in INDEX["hard"]}
    assert listed == kit_files()
    assert len(listed) >= 24
    for name in listed:
        assert (MOCK / name).with_suffix(".expected.json").is_file(), name


def test_the_reader_contract_holds_on_every_document_of_the_kit() -> None:
    reader = FixtureReader()
    check_reader_contract(reader)  # it reads every PDF and image under data/mock
    rows = [
        (f"{s['folder']}/{d['file']}", d["document_type"])
        for s in SCENARIOS
        for d in s["documents"]
    ] + [(h["file"], h["document_type"]) for h in INDEX["hard"]]
    assert any(name.startswith("hard/") for name, _ in rows)
    for name, kind in rows:
        data = (MOCK / name).read_bytes()
        pages = len(PdfReader(MOCK / name).pages) if name.endswith(".pdf") else 1
        doc = Document(id="D-kit", file=name, pages=pages)
        assert reader.classify(doc, data) == kind
        assert kind in {t.value for t in DocumentType}
        out = reader.extract(doc, data, "travel")
        assert out.fields, name
        assert all(f.source.snippet or f.source.box for f in out.fields)
