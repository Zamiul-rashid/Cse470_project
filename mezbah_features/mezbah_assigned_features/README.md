# NoteVault

A campus peer-to-peer platform for buying, renting, exchanging and giving away
study materials. CSE470 Software Engineering.

React + FastAPI + SQLite. All 20 functional requirements from the SRS are
implemented; see the [requirement map](#requirement-map) below for where each
one lives.

---

## Quick start

The only prerequisite is **miniconda**. Python, Node and every dependency are
installed into a self-contained conda environment — nothing is installed
globally.

```bash
git clone <your-repo-url> notevault
cd notevault

# 1. environment -- Python 3.11, Node and every dependency
conda env create --file environment.yml
conda activate notevault
export PYTHONPATH="$PWD/backend"

# 2. configuration -- copy the sample, then set SECRET_KEY to a random string
cp .env.example .env

# 3. storage directories
mkdir -p storage/materials storage/previews storage/qr

# 4. schema + demo data
( cd backend && alembic upgrade head && python scripts/seed.py --reset )

# 5. frontend build
( cd frontend && npm install && npm run build )

# 6. serve
( cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 )
```

Step 6 is the only one you repeat; steps 1-5 are a one-time setup.

Then open **http://localhost:8000** and sign in with a seeded account:

| Role | Email | Password |
|---|---|---|
| Admin | `admin@notevault.test` | `admin1234` |
| Student | `student1@notevault.test` | `student1234` |
| Student | `student2@notevault.test` … `student11@…` | `student1234` |

If `conda` is installed but not on your `PATH`:

```bash
export PATH="$HOME/miniconda3/bin:$PATH"
```

### Development mode

Two processes, both hot-reloading -- the API on :8000 and Vite on :5173.
Open **:5173**, not :8000:

```bash
( cd backend && uvicorn app.main:app --port 8000 --reload --workers 1 ) &
( cd frontend && npm run dev )
```

### Resetting

To wipe the database and uploaded files and start over:

```bash
rm -f notevault.db notevault.db-wal notevault.db-shm
rm -rf storage/materials/* storage/previews/* storage/qr/*
( cd backend && alembic upgrade head && python scripts/seed.py --reset )
```

---

## What runs where

```
http://localhost:8000/          the React app (production build)
http://localhost:8000/api/v1/   the REST API
http://localhost:8000/docs      Swagger UI, generated from the code
http://localhost:8000/health    liveness probe
ws://localhost:8000/ws/chat             in-app messaging
ws://localhost:8000/ws/notifications    live notification push
```

In `--dev` mode the app moves to `http://localhost:5173` and Vite proxies
`/api` and `/ws` back to port 8000.

---

## Two things to know before you demo

**1. Run the QR handoff demo on `localhost`, not a LAN IP.**
Browsers block camera access (`getUserMedia`) outside a secure context.
`http://localhost` counts as secure; `http://192.168.x.x` does not, so the
scanner will silently fail if you demo from a phone on the same network. Every
handoff therefore also prints a **6-digit manual code** that the buyer can type
in — that path works everywhere and is the safer one to show.

**2. The app runs with a single worker, deliberately.**
The WebSocket connection registry and the APScheduler rental-reminder job are
both in-process. Running `uvicorn --workers 4` would send every rental reminder
four times and give each worker a partial view of who is online. Always run
`uvicorn` with `--workers 1`.

---

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, React Router, TanStack Query |
| PDF preview | PDF.js via `react-pdf` |
| QR scanning | `html5-qrcode` (client), `qrcode` (server-side rendering) |
| Backend | FastAPI, Uvicorn, Python 3.11 |
| ORM | SQLModel (SQLAlchemy 2.0 + Pydantic) over `aiosqlite` |
| Database | SQLite in WAL mode, with an FTS5 index for keyword search |
| Migrations | Alembic |
| Auth | JWT access + rotating refresh tokens, bcrypt password hashing |
| Realtime | Native FastAPI WebSockets |
| Scheduling | APScheduler |
| Export | `csv` (stdlib) and ReportLab |
| Environment | conda (`environment.yml` carries both Python 3.11 and Node 20) |

### Two deviations from the SRS tech table, both deliberate

**SQLite + Beanie/Motor is not buildable.** The SRS Section 2 lists SQLite as the
database and Beanie/Motor as the ODM, but those are MongoDB drivers — they
cannot open a SQLite file. SQLite was kept (it is what makes "Deployment: Local
Server" and "File Storage: Local" work, and FTS5/CHECK constraints back several
requirements) and the ODM row is implemented as **SQLModel + Alembic**.

**Passwords use `bcrypt` directly rather than passlib.** passlib 1.7.4 reads
`bcrypt.__about__`, which bcrypt 4.1 removed; the resulting `AttributeError` is
a classic fresh-install failure. The wrapper passlib was providing is four lines.

---

## Repository layout

```
environment.yml  .env.example
backend/
  alembic/versions/0001_…         the entire schema, hand-written DDL
  app/core/                       config, db engine + PRAGMAs, security, deps
  app/models/                     SQLModel tables + enum vocabularies
  app/schemas/                    Pydantic request/response models
  app/services/                   business logic — the layer routers delegate to
  app/api/v1/                     HTTP routers, one per domain
  app/ws/                         connection manager + the two socket endpoints
  app/jobs/                       APScheduler setup + rental reminders
  scripts/seed.py                 demo dataset
  tests/                          pytest suite
frontend/src/
  lib/                            api client, auth context, socket hook, formatters
  components/                     shared UI
  features/{auth,listings,community,transactions,admin,analytics}/
storage/                          uploaded files, generated previews (gitignored)
notevault.db                      the database (gitignored)
```

Two architectural rules hold this together:

- **Routers never write to more than one aggregate.** Anything touching two —
  approving a listing *and* notifying matched requesters — goes through a
  service function that owns the whole unit of work.
- **Notifications are persisted first, pushed second.** Every notification is a
  row; the WebSocket push is best-effort on top, so an offline user still finds
  it on next login.

---

## Requirement map

| FR | Requirement | Where it lives |
|---|---|---|
| 1.1 | Upload study material | `api/v1/materials.py` · `services/storage.py` |
| 1.2 | Search & filter | `materials_fts` (FTS5) + the filter builder in `materials.py` |
| 1.3 | Listing type tagging | `models/enums.py` + a DB `CHECK` constraint |
| 1.4 | File preview | `services/storage.py` slices 3 pages at upload; `react-pdf` renders |
| 1.5 | Duplicate detection | `services/recommendation.py::check_duplicate` |
| 2.1 | Request board | `api/v1/requests.py` |
| 2.2 | In-app messaging | `api/v1/chat.py` + `ws/chat.py`, sharing `persist_message()` |
| 2.3 | Auto-match notifications | `services/matcher.py` → `services/notifier.py` |
| 2.4 | Bookmark / save | `api/v1/wishlist.py` |
| 2.5 | Rating & review | `api/v1/reviews.py`, gated on a `COMPLETED` transaction |
| 3.1 | Price suggestion | `services/recommendation.py::suggest_price` |
| 3.2 | Rental due-date tracking | `jobs/rental_reminders.py` |
| 3.3 | QR handoff verification | `services/qr.py` |
| 3.4 | Transaction history | `api/v1/transactions.py::GET /transactions/me` |
| 3.5 | Report content | `api/v1/reports.py` → `api/v1/admin.py` |
| 4.1 | Admin moderation panel | `api/v1/admin.py` · `services/moderation.py` |
| 4.2 | Top contributor leaderboard | `services/analytics.py::top_contributors` |
| 4.3 | Campus / branch filter | `campus_id` throughout; filter UI in Browse |
| 4.4 | Trending courses | `services/demand.py` writes, `services/analytics.py` reads |
| 4.5 | Export own report | `api/v1/exports.py` — CSV and PDF |

Login/logout and profile management are implemented too (`api/v1/auth.py`,
`api/v1/users.py`); per the project proposal they sit outside the FR count.

---

## Demo walkthrough

Runs through all 20 requirements in about ten minutes. Two browser profiles
(or one normal + one private window) let you play both sides of a transaction.

**Discovery — FR 1.1–1.5, 4.3**
1. Sign in as `student1`. On **Browse**, search `data structures`, then filter by
   course `CSE220` and type `RENT`. Switch the campus filter. *(1.2, 1.3, 4.3)*
2. Open a listing → **Preview**. Three pages render; the rest of the file is not
   in the browser. *(1.4)*
3. Go to **Upload**. Fill in title `Data Structures Notes`, course `CSE220`,
   edition `2nd`. A duplicate warning appears before you submit. *(1.5)*
4. A suggested price appears with the sample size it is based on. Attach a PDF
   and post. *(1.1, 3.1)*

**Moderation — FR 4.1**
5. Sign in as `admin` → **Admin → Moderation**. The new listing is pending.
   Approve it; the toast reports how many students were notified.

**Community — FR 2.1–2.5**
6. As `student2`, post a request on the **Request board** for `CSE220`. *(2.1)*
7. As `student1`, upload another `CSE220` item; admin approves it. `student2`'s
   notification bell lights up immediately. *(2.3)*
8. `student2` bookmarks a listing → **Wishlist**. *(2.4)*
9. `student2` opens a listing → **Message owner**, and `student1` replies. Both
   windows update live. *(2.2)*

**Exchange — FR 3.1–3.5**
10. `student2` clicks **Request this item** on a `RENT` listing, choosing 14 days.
11. `student1` opens **Transactions**, accepts, then **Generate handoff code**.
12. `student2` types the 6-digit code (or scans the QR); `student1` confirms.
    The transaction flips to COMPLETED. *(3.3)*
13. Both leave a review. `student1`'s average rating updates. *(2.5)*
14. `student2` checks **Rentals** — the due date and days-remaining chip. *(3.2)*
15. `student2` reports a listing; the admin resolves it. *(3.5, 4.1)*
16. Both check **Transactions** for the full history. *(3.4)*

**Insight — FR 4.2, 4.4, 4.5**
17. **Leaderboard** — ranked contributors with the scoring formula shown. *(4.2)*
18. **Trending** — weighted 30-day course demand. *(4.4)*
19. **Profile → Export** — download the CSV and the PDF. *(4.5)*

To see the rental reminder job fire without waiting for 8am:

```bash
cd backend && python -c "
import asyncio
from app.jobs.rental_reminders import run_rental_reminders
print(asyncio.run(run_rental_reminders()))
"
```

The seed data includes one rental due in two days, one due tomorrow and one
overdue, so all three reminder stages produce a notification.

---

## Development

```bash
conda activate notevault
export PYTHONPATH="$PWD/backend"

cd backend
pytest                              # the full suite
pytest tests/test_qr.py -v          # one file
alembic revision -m "add x"         # new migration
alembic upgrade head
python scripts/seed.py --reset      # rebuild demo data

cd ../frontend
npm run dev                         # Vite alone, expects the API on :8000
npm run build                       # type-check + production bundle
```

### Configuration

Everything is overridable in `.env` (see `.env.example`). The values worth
knowing:

| Variable | Default | Effect |
|---|---|---|
| `AUTO_APPROVE_LISTINGS` | `false` | `true` skips the moderation queue — useful when demoing uploads without an admin |
| `MAX_UPLOAD_MB` | `25` | Upload size cap |
| `PREVIEW_PAGES` | `3` | Pages sliced into the public preview |
| `QR_TTL_MINUTES` | `10` | Handoff token lifetime |
| `REMINDER_HOUR` | `8` | Local hour the rental reminder job runs |
| `ENABLE_SCHEDULER` | `true` | `false` keeps APScheduler out of the way |
| `PRICE_MIN_SAMPLE` | `3` | Below this, price suggestion returns `insufficient_data` rather than a made-up number |

---

## Known limitations

Worth stating plainly rather than discovering during the viva:

- **Copyright.** The platform hosts user-uploaded textbook scans. There is a
  notice-and-takedown path (report → admin removal) and a copyright
  acknowledgement at upload, but no proactive detection.
- **No virus scanning.** Uploads are validated by magic bytes, MIME type and a
  size cap, and are stored under generated UUIDs and never served from a
  client-supplied path — but the file contents are not scanned.
- **Single-process only.** See the note above about `--workers 1`.
- **SQLite has one writer.** Fine at demo scale with WAL enabled; a real
  deployment with hundreds of concurrent chatters would want PostgreSQL.
- **Handoff verification is a trust mechanism, not proof.** Two colluding users
  can confirm a handoff that never happened. It raises the cost of a fake
  transaction; it does not make one impossible.
