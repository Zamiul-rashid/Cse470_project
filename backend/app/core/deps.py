"""Shared FastAPI dependencies: session, current user, admin guard."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import get_session
from app.core.security import decode_token
from app.models import User, UserRole

_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated. Sign in and try again.",
    headers={"WWW-Authenticate": "Bearer"},
)


async def load_user_from_token(session: AsyncSession, token: str) -> User:
    """Shared by the HTTP dependency and the WebSocket handshake.

    Browsers cannot set headers on a WebSocket upgrade, so sockets pass the
    same access token as a query parameter and land here too.
    """
    try:
        payload = decode_token(token, expected_kind="access")
    except jwt.PyJWTError as exc:
        raise _CREDENTIALS_ERROR from exc

    user_id = payload.get("sub")
    if not user_id:
        raise _CREDENTIALS_ERROR

    user = (
        await session.execute(select(User).where(User.user_id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise _CREDENTIALS_ERROR
    return user


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise _CREDENTIALS_ERROR
    return await load_user_from_token(session, credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(current_user: CurrentUser) -> User:
    """The Admin generalization, implemented as a role check.

    One person, one identity -- ``users.role`` is the discriminator rather
    than a separate table.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is restricted to administrators.",
        )
    return current_user


AdminUser = Annotated[User, Depends(require_admin)]


async def get_optional_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User | None:
    """For endpoints that personalise their response but do not require login."""
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await load_user_from_token(session, credentials.credentials)
    except HTTPException:
        return None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]

__all__ = [
    "AdminUser",
    "CurrentUser",
    "OptionalUser",
    "SessionDep",
    "get_current_user",
    "get_optional_user",
    "load_user_from_token",
    "require_admin",
]
