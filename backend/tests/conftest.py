from pathlib import Path

import pytest

from tests.rig import make as make  # the fake-openshell fixture, shared


@pytest.fixture(autouse=True)
def _mode_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A CLAIMS_MODE of the shell must not change the tests. A test that wants the
    LangGraph agent sets it (`tests/fake_model.py::use_fake_model`). The checkpoints
    go to a temporary file, never to `.dev-data/`."""
    monkeypatch.delenv("CLAIMS_MODE", raising=False)
    monkeypatch.setenv("CLAIMS_CHECKPOINTS", str(tmp_path / "checkpoints.sqlite"))
