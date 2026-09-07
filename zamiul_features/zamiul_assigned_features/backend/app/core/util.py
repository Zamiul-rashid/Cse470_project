"""Small helpers shared across models and services."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

_PUNCT = re.compile(r"[^A-Z0-9]+")


def new_id() -> str:
    """Primary keys are opaque 32-char hex strings."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Naive UTC.

    SQLite has no timezone type, so everything is stored as naive UTC and
    rendered in the browser's locale. Mixing aware and naive datetimes is the
    usual source of ``can't compare offset-naive and offset-aware`` crashes,
    so this is the single source of "now" for the whole backend.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_course_code(raw: str | None) -> str:
    """``cse 470``, ``CSE-470`` and ``Cse470`` all collapse to ``CSE470``.

    Written into ``course_code_norm`` so duplicate detection (FR 1.5), price
    suggestion (FR 3.1), auto-match (FR 2.3) and trending (FR 4.4) all agree
    on what "the same course" means.
    """
    if not raw:
        return ""
    return _PUNCT.sub("", raw.upper())


def normalize_edition(raw: str | None) -> str:
    """Edition comparison ignores case, spacing and ordinal punctuation."""
    if not raw:
        return ""
    return _PUNCT.sub("", raw.upper())
