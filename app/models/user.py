from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum


class UserRole(StrEnum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class Office(Base):
    __tablename__ = "offices"
    __table_args__ = (UniqueConstraint("city", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    city: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # всегда в нижнем регистре
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    about: Mapped[str | None] = mapped_column(String(500))
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"))
    role: Mapped[UserRole] = mapped_column(
        str_enum(UserRole, "user_role"), server_default=UserRole.USER.value
    )
    survey_completed: Mapped[bool] = mapped_column(server_default=false())
    created_at: Mapped[datetime] = created_at_column()
