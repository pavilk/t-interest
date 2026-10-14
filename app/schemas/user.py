from pydantic import BaseModel, ConfigDict, Field

from app.models import UserRole


class OfficeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    city: str
    name: str


class MeOut(BaseModel):
    id: int
    email: str
    name: str
    about: str | None
    office: OfficeOut
    role: UserRole
    survey_completed: bool


class UpdateMeRequest(BaseModel):
    """Все поля необязательные: меняются только переданные."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    about: str | None = Field(default=None, max_length=500)
    office_id: int | None = None
