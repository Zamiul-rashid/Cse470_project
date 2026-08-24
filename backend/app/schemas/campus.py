"""Campus read model.

Campuses are reference data -- seeded, never created through the API -- so
there is no CampusCreate. This one shape populates the sign-up form, the
search filter and the analytics scope selector (FR 4.3).
"""

from __future__ import annotations

from app.schemas.common import ORMModel


class CampusRead(ORMModel):
    campus_id: str
    name: str
    location: str


__all__ = ["CampusRead"]
