"""Register / login / refresh payloads.

The password floor is 8 characters and there is no upper bound in the schema
-- ``core.security`` truncates to bcrypt's 72-byte limit deliberately, so a
long passphrase is accepted rather than rejected at the door.

Only ``TokenPair`` is a response model; the rest are request bodies and stay
plain ``BaseModel`` so nothing accidentally serialises a password back out.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel, validate_email


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    #: Plain ``str`` + regex rather than ``EmailStr`` -- see schemas.common.
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    campus_id: str

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return validate_email(value)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Name cannot be blank.")
        return stripped


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return validate_email(value)


class TokenPair(ORMModel):
    """``expires_in`` is the access token's lifetime in seconds.

    The client schedules its silent refresh off this rather than decoding the
    JWT, so the token stays opaque to the frontend.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


#: Logout revokes the same handle it was issued, so the body is identical.
LogoutRequest = RefreshRequest


__all__ = [
    "LoginRequest",
    "LogoutRequest",
    "RefreshRequest",
    "RegisterRequest",
    "TokenPair",
]
