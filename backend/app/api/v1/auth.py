"""Register, login, refresh, logout.

A stateless JWT cannot be revoked, so "logout" that only deletes the token from
localStorage leaves a valid credential in the wild for the rest of its 14 days.
Every refresh token therefore gets a server-side handle in ``refresh_tokens``
(digest only, never the token itself) and three rules follow from it:

* **Refresh rotates.** The presented row is revoked and a fresh one issued, so a
  stolen token is usable at most once before the real client's next refresh
  invalidates it.
* **Logout revokes** that row, and is idempotent -- a client retrying after a
  dropped response must not get a 404 for succeeding twice.
* **Login never says which half was wrong.** One message for "no such account"
  and for "wrong password", or the endpoint becomes an oracle for which campus
  addresses are registered.

Registration writes two rows -- the user and their single Wishlist -- in one
transaction. A user without a wishlist row would 500 the first time they
bookmarked anything, and the failure would land weeks after the cause.
"""

from __future__ import annotations

import jwt
from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.core.deps import SessionDep
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    refresh_token_expiry,
    verify_password,
)
from app.core.util import utcnow
from app.models import Campus, RefreshToken, User, UserRole, Wishlist
from app.schemas import (
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)

router = APIRouter(tags=["auth"])

#: Identical for a forged, expired, revoked and unknown refresh token. The
#: client's only correct response to any of them is "sign in again".
_INVALID_REFRESH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="That session has expired. Please sign in again.",
    headers={"WWW-Authenticate": "Bearer"},
)

_CREDENTIALS_REJECTED = "Email or password is incorrect."


async def _issue_token_pair(session: AsyncSession, user: User) -> TokenPair:
    """Mint both tokens and persist the refresh handle, then commit.

    This is the only ``commit`` in the register path: the caller adds its own
    rows first, so the user, the wishlist and the token handle all land in one
    transaction, and a crash mid-signup leaves no half-built account.
    """
    access_token = create_access_token(user.user_id, user.role)
    refresh_token = create_refresh_token(user.user_id, user.role)

    # On the register path ``user`` is still pending, and ``refresh_tokens.user_id``
    # is a real FK. No ``relationship()`` joins the two mappers, so the unit of
    # work has no dependency to order them by and falls back to the mapper sort
    # key -- alphabetical, which puts RefreshToken *before* User and makes SQLite
    # reject the insert. Flushing pins the order; it is not a commit, so the
    # caller's rows and the handle below still land in one transaction.
    await session.flush()

    session.add(
        RefreshToken(
            user_id=user.user_id,
            token_hash=hash_token(refresh_token),
            expires_at=refresh_token_expiry(),
        )
    )
    await session.commit()

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_minutes * 60,
    )


async def _load_refresh_row(session: AsyncSession, raw_token: str) -> RefreshToken:
    """Signature, kind, expiry and revocation -- all four, or 401.

    ``decode_token`` proves the string is ours and unexpired; the row proves it
    has not been rotated away or logged out. Neither check subsumes the other.
    """
    try:
        decode_token(raw_token, expected_kind="refresh")
    except jwt.PyJWTError as exc:
        raise _INVALID_REFRESH from exc

    stored = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
        )
    ).scalar_one_or_none()

    if (
        stored is None
        or stored.revoked_at is not None
        or stored.expires_at <= utcnow()
    ):
        raise _INVALID_REFRESH
    return stored


@router.post(
    "/auth/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and sign in",
)
async def register(payload: RegisterRequest, session: SessionDep) -> TokenPair:
    campus = (
        await session.execute(
            select(Campus).where(Campus.campus_id == payload.campus_id)
        )
    ).scalar_one_or_none()
    if campus is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That campus does not exist. Pick one from the list.",
        )

    existing = (
        await session.execute(select(User.user_id).where(User.email == payload.email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists. Sign in instead.",
        )

    user = User(
        name=payload.name,
        email=payload.email,  # already lowercased by the schema validator
        password_hash=hash_password(payload.password),
        # ``.value``, not the member: str(UserRole.STUDENT) renders
        # 'UserRole.STUDENT' on 3.11 and would fail the column's CHECK.
        role=UserRole.STUDENT.value,
        campus_id=campus.campus_id,
    )
    session.add(user)
    # One per user, for life. Created here so FR 2.4 never has to ask whether
    # the caller's wishlist exists yet.
    session.add(Wishlist(user_id=user.user_id))

    try:
        return await _issue_token_pair(session, user)
    except IntegrityError as exc:
        # The UNIQUE index on users.email is the real guard; the SELECT above
        # only turns the common case into a friendly message.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists. Sign in instead.",
        ) from exc


@router.post("/auth/login", response_model=TokenPair, summary="Sign in")
async def login(payload: LoginRequest, session: SessionDep) -> TokenPair:
    user = (
        await session.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    # Both branches, one message -- see the module docstring.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_CREDENTIALS_REJECTED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await _issue_token_pair(session, user)


@router.post(
    "/auth/refresh",
    response_model=TokenPair,
    summary="Exchange a refresh token for a new pair",
)
async def refresh(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    stored = await _load_refresh_row(session, payload.refresh_token)

    # The row is the authority on identity, not the token's ``sub`` claim.
    user = (
        await session.execute(select(User).where(User.user_id == stored.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise _INVALID_REFRESH

    # Rotate: the presented handle dies in the same transaction that issues its
    # replacement, so it can never be redeemed twice.
    stored.revoked_at = utcnow()
    session.add(stored)
    return await _issue_token_pair(session, user)


@router.post(
    "/auth/logout",
    response_model=MessageResponse,
    summary="Revoke a refresh token",
)
async def logout(payload: LogoutRequest, session: SessionDep) -> MessageResponse:
    stored = (
        await session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_token(payload.refresh_token)
            )
        )
    ).scalar_one_or_none()

    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = utcnow()
        session.add(stored)
        await session.commit()

    # Unknown or already-revoked handles still return 200: logging out twice is
    # not an error, and a 404 here would confirm which tokens are live.
    return MessageResponse(detail="Signed out.")


__all__ = ["router"]
