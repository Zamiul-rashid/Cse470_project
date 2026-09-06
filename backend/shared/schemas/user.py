"""Profile shapes.

``UserPublic`` is what anyone may see; ``UserMe`` adds the two fields that are
the caller's own business. ``password_hash`` appears in neither -- that split
is the whole reason schemas exist separately from tables.

``campus_name``, ``upload_count`` and the rating pair are not columns on a
single row: the router joins ``campuses`` and counts approved listings, then
feeds the assembled values in. They are declared with defaults so a cheap
read path can skip the counting query without the model failing.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models import UserRole
from app.schemas.common import ORMModel


class UserPublic(ORMModel):
    user_id: str
    name: str
    campus_id: str
    campus_name: str | None = None
    rating_avg: float = 0.0
    rating_count: int = 0
    #: APPROVED uploads only -- the number the leaderboard scores on (FR 4.2).
    upload_count: int = 0
    role: UserRole = UserRole.STUDENT


class UserMe(UserPublic):
    email: str
    created_at: datetime


class UserUpdate(BaseModel):
    """PATCH semantics: omitted means "leave alone", so everything is optional."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    campus_id: str | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("Name cannot be blank.")
        return stripped


__all__ = ["UserMe", "UserPublic", "UserUpdate"]
