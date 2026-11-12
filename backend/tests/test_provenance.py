import pytest
from pydantic import ValidationError

from app.models import ExtractedField, Source


def test_field_without_source_is_refused() -> None:
    with pytest.raises(ValidationError):
        ExtractedField.model_validate(
            {"name": "total", "value": "10", "confidence": 0.9}
        )


@pytest.mark.parametrize("page", [0, -1])
def test_page_below_one_is_refused(page: int) -> None:
    with pytest.raises(ValidationError):
        Source(document_id="d1", page=page, snippet="x")


def test_source_needs_snippet_or_box() -> None:
    with pytest.raises(ValidationError):
        Source(document_id="d1", page=1)


def test_field_with_source_is_built() -> None:
    f = ExtractedField(
        name="total",
        value="10",
        confidence=0.9,
        source=Source(document_id="d1", page=1, snippet="CHF 10"),
    )
    assert f.source.page == 1
