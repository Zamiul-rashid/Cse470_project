"""Moderation and report-resolution bodies (FR 4.1, 3.5).

``note`` is optional in the schema even though rejecting a listing requires
one. The approve and reject routes share this body, and only the reject route
enforces presence -- doing it here would either force a note on approvals or
need two near-identical models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModerationDecision(BaseModel):
    """Approve or reject a PENDING listing.

    The note is shown to the uploader, so a rejection explains itself rather
    than the listing silently vanishing.
    """

    note: str | None = Field(default=None, max_length=1000)


class ReportResolution(BaseModel):
    """Close out a report.

    ``remove_listing`` is separate from ``action`` on purpose: a report can be
    upheld without the listing coming down (a warning, a metadata fix), and
    the admin should have to opt in to the destructive half.
    """

    action: Literal["RESOLVE", "DISMISS"]
    remove_listing: bool = False
    note: str | None = Field(default=None, max_length=1000)


__all__ = ["ModerationDecision", "ReportResolution"]
