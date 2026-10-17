import pytest
from pydantic import ValidationError

from app.schemas.survey import SurveyAnswer


@pytest.mark.parametrize(
    "data",
    [{"question_id": 0}, {"question_id": 1, "option_ids": [-1]}, {"question_id": 1, "user_id": 9}],
)
def test_invalid_ids_and_extra_fields(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SurveyAnswer.model_validate(data)


def test_text_length_boundary() -> None:
    assert len(SurveyAnswer(question_id=1, text_value="x" * 300).text_value or "") == 300
    with pytest.raises(ValidationError):
        SurveyAnswer(question_id=1, text_value="x" * 301)


def test_absent_and_empty_values_are_distinct() -> None:
    assert SurveyAnswer(question_id=1).option_ids is None
    assert SurveyAnswer(question_id=1, option_ids=[]).option_ids == []
    assert SurveyAnswer(question_id=1, text_value="").text_value == ""
