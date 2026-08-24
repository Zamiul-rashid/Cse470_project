"""Fixtures for the NoteVault suite.

Four decisions shape everything below. Each of them exists because the obvious
alternative produces tests that pass while the application is broken.

* **A real, migrated SQLite file per test.** Not ``SQLModel.metadata.create_all``
  and not ``:memory:``. Every invariant that actually matters -- the
  price/listing-type CHECK, ``UNIQUE (transaction_id, reviewer_id)``, the FTS5
  index and its three sync triggers -- lives in
  ``alembic/versions/0001_initial_schema.py`` and in nothing else. A suite built
  from the SQLModel classes would test a schema the application never runs on.
  So the migration is run programmatically, per test, against a throwaway file.

* **Factories drive the real HTTP API.** ``make_user`` registers, ``make_listing``
  uploads a genuine PDF through multipart, ``make_transaction`` walks the state
  machine one PATCH at a time. Rows poked straight into the database are limited
  to the two states no endpoint can produce -- an expired QR handoff and a due
  date in the past -- and to reference data (campuses) that has no write side.

* **Background work must not escape into the developer's database.** Search,
  listing detail, request creation and the reminder job all open their own
  ``SessionLocal`` rather than the request's session, and each imported that name
  at module load. Patching ``app.core.db.SessionLocal`` alone would leave those
  copies pointing at ``notevault.db``: a green test run that quietly writes demand
  events into real data. ``_isolate_background_sessions`` rebinds every one of
  them.

* **bcrypt runs at 4 rounds.** ``hash_password`` and ``verify_password`` stay
  exactly as production has them -- this only moves the work factor. At the
  default 12 rounds the ~60 accounts this suite registers cost about 15 seconds
  of pure key stretching, and a suite people skip because it is slow protects
  nothing.
"""

from __future__ import annotations

import functools
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from types import ModuleType
from typing import Any

import bcrypt
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import select

# backend/tests/conftest.py -> tests -> backend. Mirrors alembic/env.py so the
# suite runs from the repo root as well as from backend/.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core import config as config_module  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import get_session  # noqa: E402
from app.models import Campus, ListingType, TransactionStatus, User, UserRole  # noqa: E402

# ---------------------------------------------------------------------------
# a real PDF
# ---------------------------------------------------------------------------


def build_pdf(pages: int = 1, text: str = "NoteVault sample") -> bytes:
    """A structurally valid PDF, xref offsets and all.

    ``storage.save_upload`` sniffs magic bytes, ``pypdf`` counts the pages and
    slices the preview, and ``/materials/{id}/preview`` streams the result. A
    stub that merely starts with ``%PDF-`` clears the first gate and then costs
    every listing in the suite its preview -- silently, because preview
    generation is deliberately best-effort and swallows its own failures.
    """
    objects: dict[int, bytes] = {}
    page_ids = [4 + 2 * index for index in range(pages)]

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    objects[2] = b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % pages
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for index, page_id in enumerate(page_ids):
        content_id = page_id + 1
        label = f"{text} page {index + 1}".encode("ascii", "replace")
        stream = b"BT /F1 14 Tf 20 120 Td (" + label + b") Tj ET"
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % content_id
        )
        objects[content_id] = (
            (b"<< /Length %d >>\nstream\n" % len(stream)) + stream + b"\nendstream"
        )

    document = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(document)
        document += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"

    start_xref = len(document)
    size = max(objects) + 1
    document += b"xref\n0 %d\n" % size
    # Every entry is exactly 20 bytes -- pypdf reads this table by offset, so a
    # single missing trailing space shifts the whole file.
    document += b"0000000000 65535 f \n"
    for number in range(1, size):
        document += b"%010d 00000 n \n" % offsets[number]
    document += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (size, start_xref)
    )
    return bytes(document)


@pytest.fixture(scope="session")
def make_pdf() -> Callable[..., bytes]:
    """The PDF builder, for tests that need the exact bytes (and their hash)."""
    return build_pdf


# ---------------------------------------------------------------------------
# process-wide environment
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Redirect storage, silence the scheduler, and speed up bcrypt.

    ``settings.storage_dir`` and friends are properties over the module-level
    ``REPO_ROOT``, read at call time -- so moving that one name moves uploads,
    previews, QR files *and* ``storage.resolve``'s containment check together.
    Setting the individual properties is impossible (they have no setters) and
    setting only some of them would break ``_relative()``.
    """
    patch = pytest.MonkeyPatch()
    storage_root = tmp_path_factory.mktemp("notevault-repo").resolve()

    patch.setattr(config_module, "REPO_ROOT", storage_root)
    # The scheduler is in-process: a reminder firing mid-test writes
    # notification rows nobody asked for. ASGITransport does not run the
    # lifespan either, so this is belt and braces -- and the belt matters if
    # somebody later switches to a transport that does.
    patch.setattr(settings, "enable_scheduler", False)
    # The moderation queue is the subject of half the suite, so the Phase 1
    # auto-approve flag stays off regardless of the developer's .env.
    patch.setattr(settings, "auto_approve_listings", False)
    # partial() captures the original function before the patch lands, so this
    # lowers the work factor rather than recursing.
    patch.setattr(bcrypt, "gensalt", functools.partial(bcrypt.gensalt, rounds=4))

    settings.ensure_storage_dirs()
    try:
        yield storage_root
    finally:
        patch.undo()


@pytest.fixture(scope="session")
def app(_environment: Path) -> Any:
    """The application object, imported once the environment is in place."""
    from app.main import app as fastapi_app

    return fastapi_app


# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """FOREIGN KEYS are off by default in SQLite, and the schema is full of them.

    Production sets this in ``app.core.db``; a test engine without it would
    happily accept a transaction pointing at a listing that does not exist.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


@pytest.fixture
async def db_engine(tmp_path: Path, _environment: Path) -> AsyncIterator[AsyncEngine]:
    """A fresh file, migrated to head, for one test.

    ``alembic/env.py`` overwrites ``sqlalchemy.url`` from ``settings`` on every
    run, so pointing the migration at this file means assigning
    ``settings.database_url`` -- setting it on the Config alone would be
    silently ignored.
    """
    db_path = tmp_path / "notevault-test.db"
    settings.database_url = f"sqlite+aiosqlite:///{db_path}"

    alembic_config = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(alembic_config, "head")

    engine = create_async_engine(settings.database_url, future=True)
    event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Opens sessions on the test database. Use it to read after the app wrote:
    a session held open across a request keeps stale objects in its identity map.
    """
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One session for arranging state the API cannot reach."""
    async with session_factory() as open_session:
        yield open_session


@pytest.fixture(autouse=True)
def _isolate_background_sessions(
    app: Any,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebind every module-level copy of ``SessionLocal`` onto the test database.

    ``from app.core.db import SessionLocal`` binds by value at import time, so
    ``materials``, ``requests`` and ``jobs.rental_reminders`` each hold their own
    reference. Missing one does not fail a test -- it writes into the real
    ``notevault.db`` and the assertions still pass.

    Depending on ``app`` is what makes the sweep complete: the routers only exist
    in ``sys.modules`` once the application has been imported. The reminder job
    is named explicitly because it is reachable from the scheduler rather than
    from a route, and a test calling it directly must not be the thing that
    imports it.
    """
    from app.jobs import rental_reminders  # noqa: F401  (registers the module)

    for module in list(sys.modules.values()):
        if not isinstance(module, ModuleType):
            continue
        if not getattr(module, "__name__", "").startswith("app."):
            continue
        if hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", session_factory)


@pytest.fixture
async def client(
    app: Any, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    """An ASGI-bound client whose base URL already carries the API prefix.

    Tests read as ``client.post("/materials", ...)``; the ``/api/v1`` in front of
    it comes from ``settings.api_prefix``, the same value ``main.py`` mounts on.
    """

    async def _session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as request_session:
            yield request_session

    app.dependency_overrides[get_session] = _session_override
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url=f"http://testserver{settings.api_prefix}"
    ) as http_client:
        try:
            yield http_client
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# factories
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Actor:
    """A registered account plus everything needed to act as it."""

    user_id: str
    name: str
    email: str
    password: str
    campus_id: str
    access_token: str
    refresh_token: str
    headers: dict[str, str]


CampusFactory = Callable[..., Awaitable[dict[str, str]]]
UserFactory = Callable[..., Awaitable[Actor]]
ListingFactory = Callable[..., Awaitable[dict[str, Any]]]
TransactionFactory = Callable[..., Awaitable[dict[str, Any]]]


@pytest.fixture
def make_campus(
    session_factory: async_sessionmaker[AsyncSession],
) -> CampusFactory:
    """Campuses are seeded reference data -- there is no write endpoint (FR 4.3)."""
    counter = count(1)

    async def _make(
        name: str | None = None, location: str = "Dhaka"
    ) -> dict[str, str]:
        index = next(counter)
        async with session_factory() as open_session:
            campus = Campus(name=name or f"Test Campus {index}", location=location)
            open_session.add(campus)
            await open_session.commit()
            return {
                "campus_id": campus.campus_id,
                "name": campus.name,
                "location": campus.location,
            }

    return _make


@pytest.fixture
async def campus(make_campus: CampusFactory) -> dict[str, str]:
    """The campus most tests need one of."""
    return await make_campus(name="Main Campus")


@pytest.fixture
def make_user(client: AsyncClient) -> UserFactory:
    """Register through ``POST /auth/register`` -- the wishlist row it also
    creates is a precondition for FR 2.4, and a hand-inserted user has none."""
    counter = count(1)

    async def _make(
        campus: dict[str, str],
        *,
        name: str | None = None,
        email: str | None = None,
        password: str = "correct-horse-battery",
    ) -> Actor:
        index = next(counter)
        display_name = name or f"Student {index}"
        address = email or f"student{index}@university.edu"

        response = await client.post(
            "/auth/register",
            json={
                "name": display_name,
                "email": address,
                "password": password,
                "campus_id": campus["campus_id"],
            },
        )
        assert response.status_code == 201, response.text
        tokens = response.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        me = await client.get("/users/me", headers=headers)
        assert me.status_code == 200, me.text

        return Actor(
            user_id=me.json()["user_id"],
            name=display_name,
            email=address,
            password=password,
            campus_id=campus["campus_id"],
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            headers=headers,
        )

    return _make


@pytest.fixture
def make_admin(
    make_user: UserFactory, session_factory: async_sessionmaker[AsyncSession]
) -> UserFactory:
    """An ADMIN is a ``users.role``, not a second table.

    Registration always mints a STUDENT, so the role is promoted in place. The
    token issued a moment earlier keeps working: ``require_admin`` reads the row,
    not the ``role`` claim.
    """

    async def _make(campus: dict[str, str], **kwargs: Any) -> Actor:
        kwargs.setdefault("name", "Moderator")
        actor = await make_user(campus, **kwargs)
        async with session_factory() as open_session:
            user = (
                await open_session.execute(
                    select(User).where(User.user_id == actor.user_id)
                )
            ).scalar_one()
            # ``.value``: str(UserRole.ADMIN) renders 'UserRole.ADMIN' on 3.11
            # and would fail the column's CHECK constraint.
            user.role = UserRole.ADMIN.value
            open_session.add(user)
            await open_session.commit()
        return actor

    return _make


@pytest.fixture
async def admin(campus: dict[str, str], make_admin: UserFactory) -> Actor:
    """The moderator that approvals in most tests go through."""
    return await make_admin(campus)


@pytest.fixture
def make_listing(client: AsyncClient) -> ListingFactory:
    """Upload a real PDF through multipart, optionally approving it afterwards.

    A listing lands PENDING and is invisible to everyone but its uploader and
    the moderators, so anything a second user has to *see* needs ``approve_as``.
    """
    counter = count(1)

    async def _make(
        uploader: Actor,
        *,
        title: str | None = None,
        course_code: str = "CSE321",
        department: str = "CSE",
        semester: str = "Fall 2025",
        edition: str | None = None,
        description: str | None = None,
        listing_type: ListingType | str = ListingType.SELL,
        price: float | None = 500.0,
        pages: int = 1,
        content: bytes | None = None,
        filename: str = "notes.pdf",
        approve_as: Actor | None = None,
    ) -> dict[str, Any]:
        index = next(counter)
        listing_title = title or f"Operating Systems Lecture Notes {index}"
        type_value = getattr(listing_type, "value", listing_type)

        form: dict[str, str] = {
            "title": listing_title,
            "course_code": course_code,
            "department": department,
            "semester": semester,
            "listing_type": str(type_value),
        }
        if edition is not None:
            form["edition"] = edition
        if description is not None:
            form["description"] = description
        if price is not None:
            form["price"] = str(price)

        payload = content if content is not None else build_pdf(pages=pages)
        response = await client.post(
            "/materials",
            headers=uploader.headers,
            data=form,
            files={"file": (filename, payload, "application/pdf")},
        )
        assert response.status_code == 201, response.text
        listing = response.json()

        if approve_as is not None:
            approved = await client.post(
                f"/admin/materials/{listing['listing_id']}/approve",
                headers=approve_as.headers,
                json={"note": "Looks fine."},
            )
            assert approved.status_code == 200, approved.text
            listing = approved.json()["listing"]

        return listing

    return _make


@pytest.fixture
def make_transaction(client: AsyncClient) -> TransactionFactory:
    """Create a transaction and walk it to ``status`` through the real endpoints.

    Nothing here assigns a status directly. COMPLETED in particular is only
    reachable through the QR handoff both parties confirm (FR 3.3), which is
    exactly the property the transaction tests rely on.
    """

    async def _make(
        buyer: Actor,
        listing: dict[str, Any],
        seller: Actor | None = None,
        *,
        agreed_price: float | None = None,
        rental_days: int | None = None,
        status: TransactionStatus | str = TransactionStatus.REQUESTED,
    ) -> dict[str, Any]:
        target = getattr(status, "value", status)

        body: dict[str, Any] = {"listing_id": listing["listing_id"]}
        if agreed_price is not None:
            body["agreed_price"] = agreed_price
        if rental_days is not None:
            body["rental_days"] = rental_days

        created = await client.post("/transactions", headers=buyer.headers, json=body)
        assert created.status_code == 201, created.text
        transaction = created.json()
        transaction_id = transaction["transaction_id"]

        if target == TransactionStatus.REQUESTED.value:
            return transaction

        if target == TransactionStatus.CANCELLED.value:
            cancelled = await client.patch(
                f"/transactions/{transaction_id}/status",
                headers=buyer.headers,
                json={"status": TransactionStatus.CANCELLED.value},
            )
            assert cancelled.status_code == 200, cancelled.text
            return cancelled.json()

        assert seller is not None, "driving past REQUESTED needs the seller's token"

        accepted = await client.patch(
            f"/transactions/{transaction_id}/status",
            headers=seller.headers,
            json={"status": TransactionStatus.ACCEPTED.value},
        )
        assert accepted.status_code == 200, accepted.text
        if target == TransactionStatus.ACCEPTED.value:
            return accepted.json()

        waiting = await client.patch(
            f"/transactions/{transaction_id}/status",
            headers=seller.headers,
            json={"status": TransactionStatus.AWAITING_HANDOFF.value},
        )
        assert waiting.status_code == 200, waiting.text
        if target == TransactionStatus.AWAITING_HANDOFF.value:
            return waiting.json()

        code = await client.post(
            f"/transactions/{transaction_id}/qr", headers=seller.headers
        )
        assert code.status_code == 200, code.text

        scanned = await client.post(
            f"/transactions/{transaction_id}/qr/verify",
            headers=buyer.headers,
            json={"token": code.json()["token"]},
        )
        assert scanned.status_code == 200, scanned.text

        confirmed = await client.post(
            f"/transactions/{transaction_id}/qr/verify",
            headers=seller.headers,
            json={},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == TransactionStatus.COMPLETED.value
        return confirmed.json()

    return _make
