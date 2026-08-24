"""Admin moderation panel (FR 4.1) and the report queue that closes FR 3.5.

Every route here hangs off ``AdminUser``, so the role check is a dependency
rather than an ``if`` in each handler -- the Admin generalization is implemented
as ``users.role``, and the guard is the only thing making that single-table
inheritance real.

The handlers stay thin. Approving a listing touches three aggregates (the
listing, the uploader's notification, every matched requester's notification),
so it goes through ``services.moderation`` rather than being written out here;
the router's job is the 404/409 shape and the response model. The one piece of
real logic that lives in this module is report resolution, because "resolve the
report" and "take the listing down" are two decisions the admin makes in one
click and only the router knows they arrived together.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Row, Select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.deps import AdminUser, SessionDep
from app.core.util import utcnow
from app.models import (
    Campus,
    ListingStatus,
    Report,
    ReportStatus,
    StudyMaterial,
    Transaction,
    User,
)
from app.schemas import (
    MaterialRead,
    ModerationDecision,
    ORMModel,
    Page,
    ReportRead,
    ReportResolution,
)
from app.services import moderation

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# router-local response models
# ---------------------------------------------------------------------------
# These two live here rather than in app/schemas because neither is a domain
# shape -- they are envelopes assembled by exactly one endpoint each, and
# moving them would mean importing the admin panel's layout into the schema
# package for no reuse.


class ApprovalResult(ORMModel):
    """What the admin sees after clicking approve.

    ``notified`` is the whole point: approval fires the auto-match (FR 2.3),
    and without the count the most convincing moment in that demo is invisible
    to the person who caused it. The UI renders "Approved -- 3 students
    notified".
    """

    listing: MaterialRead
    notified: int


class AdminStats(ORMModel):
    """The dashboard tiles above the two queues."""

    pending_listings: int
    open_reports: int
    users: int
    listings: int
    transactions: int


# ---------------------------------------------------------------------------
# shared query shapes
# ---------------------------------------------------------------------------


def _material_stmt() -> Select[Any]:
    """``study_materials`` joined to the two names the card needs.

    LEFT joins, not inner: a listing whose uploader or campus row went missing
    should still be moderatable -- the queue is where broken data most needs to
    be visible.
    """
    return (
        select(
            StudyMaterial,
            User.name.label("uploader_name"),
            User.rating_avg.label("uploader_rating"),
            Campus.name.label("campus_name"),
        )
        .select_from(StudyMaterial)
        .join(User, User.user_id == StudyMaterial.uploader_id, isouter=True)
        .join(Campus, Campus.campus_id == StudyMaterial.campus_id, isouter=True)
    )


def _material_read(row: Row[Any]) -> MaterialRead:
    """Row -> response model.

    ``has_preview`` is derived here because ``preview_path`` never leaves the
    server: a leaked storage path plus a static mount
    is how "preview before you buy" becomes a free download.
    """
    listing: StudyMaterial = row.StudyMaterial
    return MaterialRead.model_validate(listing).model_copy(
        update={
            "uploader_name": row.uploader_name,
            "uploader_rating": float(row.uploader_rating or 0.0),
            "campus_name": row.campus_name,
            "has_preview": listing.preview_path is not None,
        }
    )


async def _load_material_row(session: AsyncSession, listing_id: str) -> Row[Any]:
    """Fetch one listing with its display names, or 404.

    The returned row holds the live ``StudyMaterial`` instance, so the caller
    can hand it straight to a moderation service and then render the same row
    -- one query for both the mutation and the response.
    """
    row = (
        await session.execute(
            _material_stmt().where(StudyMaterial.listing_id == listing_id)
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That listing does not exist. It may have already been deleted.",
        )
    return row


def _report_stmt() -> Select[Any]:
    """``reports`` joined to the listing title and the reporter's name.

    A queue of bare ids is unusable; the admin decides on the title and who
    complained, so both are fetched with the row rather than by the client
    making N follow-up calls.
    """
    return (
        select(
            Report,
            StudyMaterial.title.label("listing_title"),
            User.name.label("reporter_name"),
        )
        .select_from(Report)
        .join(StudyMaterial, StudyMaterial.listing_id == Report.listing_id, isouter=True)
        .join(User, User.user_id == Report.reporter_id, isouter=True)
    )


def _report_read(row: Row[Any]) -> ReportRead:
    report: Report = row.Report
    return ReportRead.model_validate(report).model_copy(
        update={
            "listing_title": row.listing_title,
            "reporter_name": row.reporter_name,
        }
    )


async def _count(session: AsyncSession, stmt: Select[Any]) -> int:
    """COUNT(*) for the paged queries, sharing their WHERE clause."""
    return int((await session.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# moderation queue (FR 4.1)
# ---------------------------------------------------------------------------


@router.get("/moderation/queue", response_model=Page[MaterialRead])
async def moderation_queue(
    session: SessionDep,
    _admin: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Page[MaterialRead]:
    """Listings awaiting a decision, oldest first.

    Oldest-first is the requirement and also the only fair order: a queue
    sorted newest-first starves the uploads at the bottom, which is precisely
    the failure a moderation panel exists to prevent.
    """
    where = StudyMaterial.status == ListingStatus.PENDING

    total = await _count(
        session, select(func.count()).select_from(StudyMaterial).where(where)
    )
    rows = (
        await session.execute(
            _material_stmt()
            .where(where)
            .order_by(StudyMaterial.upload_date.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return Page.build(
        [_material_read(row) for row in rows], total, page, page_size
    )


@router.post("/materials/{listing_id}/approve", response_model=ApprovalResult)
async def approve_material(
    listing_id: str,
    session: SessionDep,
    admin: AdminUser,
    decision: ModerationDecision | None = None,
) -> ApprovalResult:
    """Publish a pending listing and auto-match it against open requests.

    The matcher runs inline rather than in a ``BackgroundTask``: the response
    carries the number of students notified, and a background task cannot be
    counted before it has run. The fan-out is capped in the matcher, so this
    stays a bounded unit of work.

    Re-approving is a 409 from the service, not a silent no-op -- otherwise a
    double-click re-notifies every requester.
    """
    row = await _load_material_row(session, listing_id)
    note = decision.note if decision else None
    notified = await moderation.approve_listing(session, row.StudyMaterial, admin, note)
    return ApprovalResult(listing=_material_read(row), notified=notified)


@router.post("/materials/{listing_id}/reject", response_model=MaterialRead)
async def reject_material(
    listing_id: str,
    decision: ModerationDecision,
    session: SessionDep,
    admin: AdminUser,
) -> MaterialRead:
    """Reject a pending listing. The note is required.

    ``ModerationDecision.note`` is optional in the schema because approve and
    reject share one body; ``moderation.reject_listing`` is what enforces it
    here, with a 422 naming the field. A rejection with no reason is
    indistinguishable from the site being broken.
    """
    row = await _load_material_row(session, listing_id)
    await moderation.reject_listing(session, row.StudyMaterial, admin, decision.note or "")
    return _material_read(row)


# ---------------------------------------------------------------------------
# report queue (FR 3.5 read side, FR 4.1)
# ---------------------------------------------------------------------------


@router.get("/reports", response_model=Page[ReportRead])
async def list_reports(
    session: SessionDep,
    _admin: AdminUser,
    report_status: ReportStatus | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Page[ReportRead]:
    """The report queue, oldest first, optionally filtered by status.

    The filter is a query alias rather than a path segment so the frontend's
    tab bar (Open / Resolved / Dismissed) is one endpoint with one hook.
    Unfiltered, the whole history comes back -- an admin auditing what a
    colleague dismissed last week needs that.
    """
    count_stmt = select(func.count()).select_from(Report)
    stmt = _report_stmt()
    if report_status is not None:
        count_stmt = count_stmt.where(Report.status == report_status)
        stmt = stmt.where(Report.status == report_status)

    total = await _count(session, count_stmt)
    rows = (
        await session.execute(
            stmt.order_by(Report.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return Page.build([_report_read(row) for row in rows], total, page, page_size)


@router.post("/reports/{report_id}/resolve", response_model=ReportRead)
async def resolve_report(
    report_id: str,
    resolution: ReportResolution,
    session: SessionDep,
    admin: AdminUser,
) -> ReportRead:
    """Close a report, optionally taking the listing down with it.

    ``remove_listing`` is deliberately independent of ``action``: a report can
    be upheld without the listing coming down (a warning, a metadata fix), so
    the destructive half is opt-in. Dismissing *and* removing is contradictory
    and rejected rather than guessed at.

    The uploader is notified by ``moderation.remove_listing``, which owns that
    message -- notifying again here would tell them twice about one takedown.
    The reporter is not notified: a reporter who learns the outcome of every
    report learns who they can get taken down, which is its own abuse vector.
    """
    row = (
        await session.execute(_report_stmt().where(Report.report_id == report_id))
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That report does not exist.",
        )

    report: Report = row.Report
    if report.status != ReportStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This report was already {report.status.lower()}. "
                "Reopening a closed report is not supported."
            ),
        )
    if resolution.remove_listing and resolution.action == "DISMISS":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Dismissing a report means no action was needed. "
                "Choose RESOLVE to remove the listing."
            ),
        )

    note = (resolution.note or "").strip() or None

    if resolution.remove_listing:
        listing = (
            await session.execute(
                select(StudyMaterial).where(StudyMaterial.listing_id == report.listing_id)
            )
        ).scalar_one_or_none()
        if listing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The reported listing no longer exists, so it cannot be removed.",
            )
        # Already down: a second report against the same listing is the normal
        # case, and the first admin's takedown must not block this one from
        # closing their report.
        if listing.status != ListingStatus.REMOVED:
            await moderation.remove_listing(session, listing, admin, note)

    report.status = (
        ReportStatus.RESOLVED if resolution.action == "RESOLVE" else ReportStatus.DISMISSED
    )
    report.resolved_by = admin.user_id
    report.resolved_at = utcnow()
    report.resolution_note = note
    session.add(report)
    await session.commit()
    await session.refresh(report)

    return _report_read(row)


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=AdminStats)
async def admin_stats(session: SessionDep, _admin: AdminUser) -> AdminStats:
    """The five numbers on the admin landing page.

    One statement with five scalar subqueries rather than five round trips --
    this renders on every admin page load, and SQLite is a single writer that
    should not be queued behind a dashboard.
    """

    def _total(model: Any, *where: Any) -> Any:
        return select(func.count()).select_from(model).where(*where).scalar_subquery()

    row = (
        await session.execute(
            select(
                _total(StudyMaterial, StudyMaterial.status == ListingStatus.PENDING).label(
                    "pending_listings"
                ),
                _total(Report, Report.status == ReportStatus.OPEN).label("open_reports"),
                _total(User).label("users"),
                _total(StudyMaterial).label("listings"),
                _total(Transaction).label("transactions"),
            )
        )
    ).one()

    return AdminStats(
        pending_listings=int(row.pending_listings),
        open_reports=int(row.open_reports),
        users=int(row.users),
        listings=int(row.listings),
        transactions=int(row.transactions),
    )


__all__ = ["AdminStats", "ApprovalResult", "router"]
