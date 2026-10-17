from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models import QuestionType

PositiveId = Annotated[int, Field(gt=0)]


class SurveyAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: PositiveId
    option_ids: list[PositiveId] | None = None
    text_value: str | None = Field(default=None, max_length=300)


class AnswerOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    order: int
    interest_id: int | None


class QuestionOut(BaseModel):
    id: int
    text: str
    type: QuestionType
    order: int
    options: list[AnswerOptionOut]


class InterestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class InterestDirectionOut(InterestOut):
    children: list[InterestOut]


class MatchingProfile(BaseModel):
    interest_ids: set[int]
    office_id: int
    city: str
