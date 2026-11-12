import pytest


@pytest.fixture(autouse=True)
def _mode_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CLAIMS_MODE of the shell must not change the tests. A test that wants the
    LLM agent sets it (`tests/fake_chat.py::use_fake_model`)."""
    monkeypatch.delenv("CLAIMS_MODE", raising=False)
