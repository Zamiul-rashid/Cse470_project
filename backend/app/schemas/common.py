"""Pieces every other schema module leans on.

Two things live here rather than being repeated a dozen times:

* ``ORMModel`` -- the ``from_attributes=True`` base every response model
  inherits. Responses are built straight from SQLModel rows (and from
  ``Row`` tuples of an explicit join), so pydantic has to be allowed to read
  attributes rather than dict keys.
* ``Page[T]`` -- one pagination envelope for the whole API. A single shape
  means the frontend writes one ``usePagedQuery`` hook instead of six.

The email validator is here too because ``pydantic[email]`` is not installed:
``EmailStr`` would import ``email_validator`` and blow up at import time, so a
plain regex does the job. It rejects the obvious garbage; the real proof that
an address exists is a mail we never send.
"""

from __future__ import annotations

import re
from math import ceil
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

#: Deliberately permissive: one @, no whitespace, a dotted TLD of 2+ letters.
#: Anything stricter starts rejecting valid campus addresses.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def validate_email(raw: str) -> str:
    """Lowercase and trim, or raise ``ValueError`` for pydantic to turn into a 422.

    Addresses are stored lowercased because ``users.email`` is UNIQUE and
    SQLite compares TEXT case-sensitively -- without this, ``A@x.edu`` and
    ``a@x.edu`` are two accounts.
    """
    value = raw.strip().lower()
    if not EMAIL_RE.match(value):
        raise ValueError("Enter a valid email address, e.g. name@university.edu")
    return value


class ORMModel(BaseModel):
    """Base for every response model in this package."""

    model_config = ConfigDict(from_attributes=True)


class MessageResponse(ORMModel):
    """The generic acknowledgement for endpoints with nothing else to say."""

    detail: str


class Page(BaseModel, Generic[T]):
    """Uniform pagination envelope.

    ``pages`` is returned pre-computed so the client never has to know the
    ceiling-division rule (and never disagrees with the server about it).
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    pages: int = Field(ge=0)

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> Page[T]:
        """Construct from the two halves of a paged query: rows + COUNT(*)."""
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=ceil(total / page_size) if page_size > 0 else 0,
        )


__all__ = [
    "EMAIL_RE",
    "MessageResponse",
    "ORMModel",
    "Page",
    "validate_email",
]
