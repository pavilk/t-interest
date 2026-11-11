from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.survey import InterestOut
from app.schemas.user import PublicProfile


class PersonRecommendationOut(BaseModel):
    user: PublicProfile
    score: float
    common_interests: list[InterestOut]
    common_categories: list[InterestOut] = Field(
        description="Общие категории интересов, выбранные в анкете"
    )
    same_office: bool


class LikeOut(BaseModel):
    matched: Literal[True] = True
    chat_id: int | None = None
