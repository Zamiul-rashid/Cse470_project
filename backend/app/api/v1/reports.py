"""Reporting a listing (FR 3.5).

The filing half of notice-and-takedown; the admin queue that closes each report
lives in ``api/v1/admin.py``. A report is worth nothing unless a human sees it,
so filing one is not a quiet insert -- every admin gets a notification with the
listing attached, and the reporter can watch their own reports in
``GET /reports/me`` rather than wondering whether anything happened.

The one-per-user-per-listing rule is a UNIQUE index in the migration, not a
convention. This module checks for the existing row first so the caller gets a
409 that explains itself instead of a 500 from the constraint.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep
from app.models import (
    NotificationType,
    Report,
    ReportStatus,
    StudyMaterial,
    User,
    UserRole,
)
from app.schemas import Page, ReportCreate, ReportRead
from app.services.notifier import notify_many

router = APIRouter(prefix="/reports", tags=["reports"])


def _report_read(
    report: Report,
    listing_title: str | None = None,
    reporter_name: str | None = None,
) -> ReportRead:
    """``resolved_by`` is deliberately absent: the reporter sees that a decision
    was made and what the note says, never which moderator made it."""
    return ReportRead(
        report_id=report.report_id,
        listing_id=report.listing_id,
        listing_title=listing_title,
        reporter_id=report.reporter_id,
        reporter_name=reporter_name,
        reason=report.reason,
        details=report.details,
        status=report.status,
        created_at=report.created_at,
        resolved_at=report.resolved_at,
        resolution_note=report.resolution_note,
    )


async def _admin_ids(session: AsyncSession) -> list[str]:
    return list(
        (
            await session.execute(
                select(User.user_id).where(User.role == UserRole.ADMIN.value)
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "",
    response_model=ReportRead,
    status_code=status.HTTP_201_CREATED,
    summary="Report a listing",
)
async def create_report(
    payload: ReportCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReportRead:
    """File one report against one listing.

    Reporting your own listing is a 400 rather than silently allowed: it does
    nothing but add noise to the moderation queue, and the owner already has
    ``DELETE /materials/{id}``.
    """
    listing = (
        await session.execute(
            select(StudyMaterial).where(StudyMaterial.listing_id == payload.listing_id)
        )
    ).scalar_one_or_none()
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That listing does not exist.",
        )
    if listing.uploader_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This is your own listing. Edit or delete it instead of reporting it."
            ),
        )

    existing = (
        await session.execute(
            select(Report).where(
                Report.reporter_id == current_user.user_id,
                Report.listing_id == payload.listing_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # UNIQUE (reporter_id, listing_id) is one report per user per listing
        # *ever*, so an already-decided report blocks a second one too. Saying
        # so is more useful than letting the insert raise.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "You have already reported this listing and a moderator is "
                "looking at it."
                if existing.status == ReportStatus.OPEN
                else "You have already reported this listing and it has been reviewed."
            ),
        )

    report = Report(
        reporter_id=current_user.user_id,
        listing_id=listing.listing_id,
        reason=payload.reason.value,
        details=payload.details,
        status=ReportStatus.OPEN,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)

    # Every admin, not one on rotation: there is no assignment model, and a
    # report nobody was told about is the same as no report at all.
    await notify_many(
        session,
        await _admin_ids(session),
        # The vocabulary in models/enums.py has no REPORT_FILED, and that file
        # is fixed -- moderation is the right channel, and ref_id points at the
        # listing so the bell menu lands on the thing to judge.
        type=NotificationType.MODERATION_RESULT,
        message=(
            f"{current_user.name} reported '{listing.title}' "
            f"({payload.reason.value})."
        ),
        ref_type="listing",
        ref_id=listing.listing_id,
    )
    return _report_read(report, listing.title, current_user.name)


@router.get("/me", response_model=Page[ReportRead], summary="Reports I filed")
async def list_my_reports(
    session: SessionDep,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ReportRead]:
    """Newest first -- the reporter is checking on what they just filed."""
    condition = Report.reporter_id == current_user.user_id

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(Report).where(condition)
            )
        ).scalar_one()
    )
    rows = (
        await session.execute(
            select(Report, StudyMaterial.title)
            .join(
                StudyMaterial,
                StudyMaterial.listing_id == Report.listing_id,
                isouter=True,
            )
            .where(condition)
            .order_by(Report.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = [
        _report_read(report, title, current_user.name) for report, title in rows
    ]
    return Page.build(items, total, page, page_size)


__all__ = ["router"]
