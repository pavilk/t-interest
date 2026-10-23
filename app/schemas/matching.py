from typing import Literal

from pydantic import BaseModel

from app.schemas.survey import InterestOut
from app.schemas.user import PublicProfile


class PersonRecommendationOut(BaseModel):
    user: PublicProfile
    score: float
    common_interests: list[InterestOut]
    same_office: bool


class LikeOut(BaseModel):
    matched: Literal[True] = True
    chat_id: int | None = None
