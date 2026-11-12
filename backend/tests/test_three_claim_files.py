"""The three demo claim files of docs/MOCK-DATA.md give the three outcomes."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests import test_health_api, test_property_api, test_travel_api


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def test_travel_property_and_health_give_the_three_outcomes(
    client: TestClient,
) -> None:
    runs = [
        (test_travel_api.run_claim, "straight_through", 0, False),
        (test_property_api.run_claim, "handler_review", 0, False),
        (test_health_api.run_claim, "investigation", 130, True),
    ]
    for run, outcome, score, has_report in runs:
        c = client.get(f"/claims/{run(client)}").json()
        assert (c["outcome"], c["score"]) == (outcome, score)
        assert (c["fraud_report"] is not None) is has_report
