"""Personal history export, CSV or PDF (FR 4.5).

An export is where one user's history can leak into another's, so read this
module before changing it. The single most important line in it is the route
itself: **``/me/export``, never ``/users/{id}/export``**. The subject of the
export is read from the access token by the ``CurrentUser`` dependency, so
there is no user id anywhere in the signature -- no path segment, no query
parameter, no field in a body. An endpoint that accepts an id has to be right
about authorization on every future edit; an endpoint that cannot express
someone else's id is right permanently, and the authorization test in section 9
has nothing left to catch it out on.

Both formats render the same two sections from the same row builders, so the
CSV and the PDF can never drift into disagreeing about a user's history.

* **CSV** streams. ``csv.writer`` writes into one small ``StringIO`` that is
  truncated after every yield, so the assembled document never exists in
  memory -- only the row being formatted.
* **PDF** cannot stream (ReportLab needs the whole document to lay out page
  breaks), so it is built into a ``BytesIO`` and returned whole. At one
  student's history that is kilobytes.

The rows themselves are fetched up front rather than streamed from a cursor:
the request's session is closed once the handler returns, and a generator that
is still reading from it while the response streams is a use-after-close bug
that only shows up under load.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import or_
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep
from app.core.util import utcnow
from app.models import Campus, StudyMaterial, Transaction, User

router = APIRouter(tags=["export"])

LISTING_HEADERS: tuple[str, ...] = (
    "listing_id",
    "title",
    "course_code",
    "department",
    "semester",
    "edition",
    "listing_type",
    "price",
    "status",
    "pages",
    "uploaded",
)

TRANSACTION_HEADERS: tuple[str, ...] = (
    "transaction_id",
    "listing",
    "role",
    "counterparty",
    "type",
    "agreed_price",
    "status",
    "started",
    "completed",
)


def _moment(value: datetime | None) -> str:
    """One date format across both sections and both file formats.

    Minute precision: everything is stored as naive UTC, and seconds on a
    handoff timestamp are noise in a document a student reads.
    """
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _money(value: float | None) -> str:
    """Blank, not "0.00", for EXCHANGE and FREE -- those genuinely have no price."""
    return f"{value:.2f}" if value is not None else ""


def _listing_rows(listings: list[StudyMaterial]) -> list[list[str]]:
    return [
        [
            listing.listing_id,
            listing.title,
            listing.course_code,
            listing.department,
            listing.semester,
            listing.edition or "",
            listing.listing_type,
            _money(listing.price),
            listing.status,
            str(listing.page_count) if listing.page_count is not None else "",
            _moment(listing.upload_date),
        ]
        for listing in listings
    ]


def _transaction_rows(rows: list[Any], user_id: str) -> list[list[str]]:
    """``role`` and ``counterparty`` are resolved here, not left to the reader.

    A transaction row holds a buyer id and a seller id; which one is "me"
    decides how the whole line reads, and an exported id the student has to
    match against their own is not an answer.
    """
    out: list[list[str]] = []
    for row in rows:
        transaction: Transaction = row.Transaction
        is_buyer = transaction.buyer_id == user_id
        out.append(
            [
                transaction.transaction_id,
                row.listing_title or "",
                "buyer" if is_buyer else "seller",
                (row.seller_name if is_buyer else row.buyer_name) or "",
                transaction.transaction_type,
                _money(transaction.agreed_price),
                transaction.status,
                _moment(transaction.transaction_date),
                _moment(transaction.completed_at),
            ]
        )
    return out


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _csv_stream(
    heading: list[str],
    listing_rows: list[list[str]],
    transaction_rows: list[list[str]],
) -> Iterator[str]:
    """Yield the document a row at a time.

    ``csv.writer`` needs a file-like object, so it writes into a ``StringIO``
    that is drained and truncated after each yield -- that buffer holds one row
    at a time and nothing accumulates. Writing through ``csv.writer`` rather
    than joining with commas is what makes a title containing a comma or a
    quote survive the round trip into Excel.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def drain() -> str:
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    writer.writerow(heading)
    yield drain()

    for title, headers, rows in (
        ("LISTINGS", LISTING_HEADERS, listing_rows),
        ("TRANSACTIONS", TRANSACTION_HEADERS, transaction_rows),
    ):
        # Blank line then a section title then the header row: two tables in
        # one file, and a reader (or a spreadsheet import) needs the boundary.
        writer.writerow([])
        writer.writerow([title, f"{len(rows)} row(s)"])
        writer.writerow(list(headers))
        yield drain()

        for row in rows:
            writer.writerow(row)
            yield drain()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

#: Landscape A4: eleven listing columns do not fit portrait without shrinking
#: the type to something nobody reads.
_PAGE_SIZE = landscape(A4)
_CONTENT_WIDTH = _PAGE_SIZE[0] - 30 * mm

_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        # Zebra striping so a wide row is still readable across the page.
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
)


def _pdf_section(
    title: str,
    headers: tuple[str, ...],
    rows: list[list[str]],
    empty_message: str,
    styles: Any,
) -> list[Any]:
    """One heading plus either a table or an honest empty state.

    A brand-new account exports two empty sections, and a PDF containing two
    bare column headers reads as a broken export. "No listings yet." is the
    same information without the doubt.
    """
    flow: list[Any] = [Paragraph(title, styles["section"]), Spacer(1, 3 * mm)]
    if not rows:
        flow.extend([Paragraph(empty_message, styles["empty"]), Spacer(1, 8 * mm)])
        return flow

    # Every cell becomes a Paragraph so long titles wrap inside the column
    # instead of running under the next one.
    body = [[Paragraph(str(cell), styles["cell"]) for cell in row] for row in rows]
    head = [[Paragraph(header, styles["head"]) for header in headers]]

    table = Table(
        head + body,
        colWidths=[_CONTENT_WIDTH / len(headers)] * len(headers),
        repeatRows=1,
    )
    table.setStyle(_TABLE_STYLE)
    flow.extend([table, Spacer(1, 8 * mm)])
    return flow


def _build_pdf(
    user: User,
    campus_name: str | None,
    listing_rows: list[list[str]],
    transaction_rows: list[list[str]],
) -> bytes:
    """Title block, listings table, transactions table."""
    sheet = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("nv-title", parent=sheet["Title"], fontSize=18, spaceAfter=2),
        "meta": ParagraphStyle(
            "nv-meta", parent=sheet["Normal"], fontSize=9, textColor=colors.HexColor("#475569")
        ),
        "section": ParagraphStyle(
            "nv-section", parent=sheet["Heading2"], fontSize=12, spaceBefore=0
        ),
        "head": ParagraphStyle(
            "nv-head", parent=sheet["Normal"], fontSize=7.5, textColor=colors.white
        ),
        "cell": ParagraphStyle("nv-cell", parent=sheet["Normal"], fontSize=7.5, leading=9),
        "empty": ParagraphStyle(
            "nv-empty",
            parent=sheet["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#64748b"),
        ),
    }

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=_PAGE_SIZE,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"NoteVault history -- {user.name}",
        author="NoteVault",
    )

    flow: list[Any] = [
        Paragraph("NoteVault -- my history", styles["title"]),
        Paragraph(
            f"{user.name} &nbsp;·&nbsp; {campus_name or 'Campus unknown'} "
            f"&nbsp;·&nbsp; generated {_moment(utcnow())} UTC",
            styles["meta"],
        ),
        Spacer(1, 8 * mm),
    ]
    flow += _pdf_section(
        "Listings", LISTING_HEADERS, listing_rows, "No listings yet.", styles
    )
    flow += _pdf_section(
        "Transactions", TRANSACTION_HEADERS, transaction_rows, "No transactions yet.", styles
    )

    document.build(flow)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------


@router.get(
    "/me/export",
    response_class=Response,
    responses={
        200: {
            "description": "The caller's own listings and transactions.",
            "content": {"text/csv": {}, "application/pdf": {}},
        }
    },
)
async def export_my_history(
    session: SessionDep,
    current_user: CurrentUser,
    format: str = Query(  # noqa: A002 -- the query parameter is named `format`
        "csv", pattern="^(csv|pdf)$", description="csv or pdf"
    ),
) -> Response:
    """Export everything this account has listed and traded.

    There is no ``user_id`` parameter, and adding one would be a security
    regression rather than a feature: the subject of the export is
    ``current_user``, decoded from the bearer token, so the only history any
    caller can name is their own.
    """
    user_id = current_user.user_id

    listings = list(
        (
            await session.execute(
                select(StudyMaterial)
                .where(StudyMaterial.uploader_id == user_id)
                .order_by(StudyMaterial.upload_date.desc())
            )
        )
        .scalars()
        .all()
    )

    # Aliased twice: buyer and seller are both `users`, and one join cannot
    # supply two different names from the same table.
    buyer = aliased(User)
    seller = aliased(User)
    transaction_rows = (
        await session.execute(
            select(
                Transaction,
                StudyMaterial.title.label("listing_title"),
                buyer.name.label("buyer_name"),
                seller.name.label("seller_name"),
            )
            .select_from(Transaction)
            .join(
                StudyMaterial,
                StudyMaterial.listing_id == Transaction.listing_id,
                isouter=True,
            )
            .join(buyer, buyer.user_id == Transaction.buyer_id, isouter=True)
            .join(seller, seller.user_id == Transaction.seller_id, isouter=True)
            # Both sides of the exchange: "my history" is what I bought and
            # what I sold, not one half of it.
            .where(or_(Transaction.buyer_id == user_id, Transaction.seller_id == user_id))
            .order_by(Transaction.transaction_date.desc())
        )
    ).all()

    campus_name = (
        await session.execute(
            select(Campus.name).where(Campus.campus_id == current_user.campus_id)
        )
    ).scalar_one_or_none()

    listing_table = _listing_rows(listings)
    transaction_table = _transaction_rows(list(transaction_rows), user_id)
    generated = utcnow()
    filename = f"notevault-history-{generated.date().isoformat()}"

    if format == "pdf":
        return Response(
            content=_build_pdf(current_user, campus_name, listing_table, transaction_table),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
        )

    heading = [
        "NoteVault history export",
        current_user.name,
        campus_name or "",
        f"generated {_moment(generated)} UTC",
    ]
    return StreamingResponse(
        _csv_stream(heading, listing_table, transaction_table),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


__all__ = ["LISTING_HEADERS", "TRANSACTION_HEADERS", "router"]
