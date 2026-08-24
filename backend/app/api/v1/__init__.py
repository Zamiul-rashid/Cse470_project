"""The single ``/api/v1`` router, assembled from the per-module routers.

One place where every route in the system becomes reachable, so ``main.py``
mounts exactly one object and nobody has to remember to wire up a new module in
two files.

Two conventions are load-bearing here:

* **Tags belong to the routers, not to this file.** Every module declares its
  own ``APIRouter(tags=[...])``; passing ``tags=`` to ``include_router`` would
  *append* to that list and give each operation a duplicated tag in the
  OpenAPI document. The descriptions for those tags live in
  ``main.tags_metadata``.
* **Order is match order.** Starlette tries routes in registration order and
  takes the first match, so modules whose paths could shadow another module's
  are included after it. ``auth`` and ``users`` come first because they own
  fixed literal paths; ``exports`` (``/me/export``) comes last because it is
  the only route in its namespace and nothing may sit in front of it.

The include order below follows the four SRS modules, which is also the order
the tags appear in on the Swagger page.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.campuses import router as campuses_router
from app.api.v1.chat import router as chat_router
from app.api.v1.exports import router as exports_router
from app.api.v1.materials import router as materials_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.rentals import router as rentals_router
from app.api.v1.reports import router as reports_router
from app.api.v1.requests import router as requests_router
from app.api.v1.reviews import router as reviews_router
from app.api.v1.transactions import router as transactions_router
from app.api.v1.users import router as users_router
from app.api.v1.wishlist import router as wishlist_router

api_router = APIRouter()

# --- identity: outside the FR count, needed by everything else -------------
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(campuses_router)

# --- module 1: core listing & discovery (FR 1.1-1.5) -----------------------
api_router.include_router(materials_router)

# --- module 2: community & communication (FR 2.1-2.5) ----------------------
api_router.include_router(requests_router)
api_router.include_router(wishlist_router)
api_router.include_router(chat_router)
api_router.include_router(notifications_router)
api_router.include_router(reviews_router)

# --- module 3: transactions & trust (FR 3.1-3.5) ---------------------------
api_router.include_router(transactions_router)
api_router.include_router(rentals_router)
api_router.include_router(reports_router)

# --- module 4: admin, analytics & insights (FR 4.1-4.5) --------------------
api_router.include_router(admin_router)
api_router.include_router(analytics_router)
api_router.include_router(exports_router)

__all__ = ["api_router"]
