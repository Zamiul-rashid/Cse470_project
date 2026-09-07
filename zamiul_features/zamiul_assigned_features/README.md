# Zamiul's NoteVault Features — Complete Viva Guide

**Read this if you know nothing about the project.** It explains the four
features assigned to Zamiul from the ground up: what they do, how a click in the
browser becomes a row in the database, every function involved on both sides,
how to run it, how to open the database, how the tests work, and what to say
when a grader asks.

The four features are:

| # | Module | Feature | Requirement ID |
|---|---|---|---|
| 1 | Core Listing & Discovery | **Upload Study Material** | FR 1.1 |
| 2 | Community & Communication | **Request Board** | FR 2.1 |
| 3 | Transactions & Trust | **Price Suggestion Engine** | FR 3.1 |
| 4 | Admin, Analytics & Insights | **Admin Moderation Panel** | FR 4.1 |

These four are not four unrelated things. They form **one loop**, and that is
the single most useful thing to be able to say in a viva:

> A student **uploads** material (FR 1.1). While filling in the form the system
> tells them what similar items sold for (**FR 3.1**). The upload does not go
> live — it lands in the **admin moderation queue** (FR 4.1). When the admin
> approves it, the system checks the **request board** (FR 2.1) and notifies
> every student who had posted a wanted-ad for that course.

If you remember only one sentence, remember that one.

---

## Table of contents

1. [What this folder is](#1-what-this-folder-is)
2. [Crash course: the words you need](#2-crash-course-the-words-you-need)
3. [The architecture in one picture](#3-the-architecture-in-one-picture)
4. [How to run it](#4-how-to-run-it)
5. [How to open and read the database](#5-how-to-open-and-read-the-database)
6. [The life of one request (generic)](#6-the-life-of-one-request-generic)
7. [Feature 1 — Upload Study Material (FR 1.1)](#7-feature-1--upload-study-material-fr-11)
8. [Feature 2 — Request Board (FR 2.1)](#8-feature-2--request-board-fr-21)
9. [Feature 3 — Price Suggestion Engine (FR 3.1)](#9-feature-3--price-suggestion-engine-fr-31)
10. [Feature 4 — Admin Moderation Panel (FR 4.1)](#10-feature-4--admin-moderation-panel-fr-41)
11. [How the four features connect](#11-how-the-four-features-connect)
12. [Database tables used by these features](#12-database-tables-used-by-these-features)
13. [Complete API reference](#13-complete-api-reference)
14. [The tests: how they work and what they prove](#14-the-tests-how-they-work-and-what-they-prove)
15. [Function index — backend](#15-function-index--backend)
16. [Function index — frontend](#16-function-index--frontend)
17. [Viva questions and answers](#17-viva-questions-and-answers)
18. [Live demo script](#18-live-demo-script)
19. [Troubleshooting](#19-troubleshooting)

---

## 1. What this folder is

This is a **transfer bundle**, not a separate application. Every file in here is
a **byte-for-byte copy** of a file from the main NoteVault repository, kept at
its original path so you can see exactly where it belongs.

```
zamiul_features/zamiul_assigned_features/
├── MANIFEST.md      ← which files belong to which feature
├── README.md        ← this guide
├── environment.yml  ← the conda environment (Python 3.11 + Node 20)
├── backend/         ← same layout as the real backend/
└── frontend/        ← same layout as the real frontend/
```

**You cannot `cd` into this folder and run the app.** It has no `.env`, no
`alembic.ini`, no `storage/` folder, and it deliberately omits the ~80 files
that belong to other team members' features. To actually run anything, use the
main repository at `Cse470_project/`. This folder exists so a grader (or a
teammate) can see Zamiul's work isolated without hunting through 200 files.

### Why some files appear under more than one feature in MANIFEST.md

Because they genuinely implement more than one requirement. `materials.py` holds
both the upload endpoint (FR 1.1) *and* the price-suggestion endpoint (FR 3.1).
`models/tables.py` defines every table in the system. `App.tsx` routes every
page. Splitting these by feature would mean cutting working files in half. The
honest answer in a viva is: **"features overlap at the file level; the MANIFEST
lists which functions inside each file are mine."**

---

## 2. Crash course: the words you need

If any of these are unfamiliar, read this section first. Everything below
assumes them.

### Backend words

| Term | What it actually means here |
|---|---|
| **FastAPI** | The Python web framework. You write a normal Python function, put `@router.post("/materials")` above it, and FastAPI turns it into an HTTP endpoint. It reads the function's type hints to validate input and generate documentation automatically. |
| **Endpoint / route** | One URL + one HTTP method. `POST /api/v1/materials` is an endpoint. |
| **Router** | A group of related endpoints in one file. `materials.py` has a router with 9 endpoints on it. |
| **`async def` / `await`** | Python's way of doing many things at once on one thread. When the code says `await session.execute(...)`, it means "start this database query and let other requests run while we wait for it." Every endpoint in this project is `async`. |
| **SQLModel** | The ORM (Object-Relational Mapper). It lets you write `StudyMaterial(title="Notes")` in Python instead of `INSERT INTO study_materials ...` in SQL. It is SQLAlchemy + Pydantic combined. |
| **Session** | One conversation with the database. You add objects to it, then `commit()` to save. If anything fails, `rollback()` undoes everything since the last commit. |
| **Pydantic / schema** | A class that describes what data is *allowed* to look like. If a request body does not match, FastAPI rejects it with HTTP 422 before your code ever runs. |
| **Alembic** | Migration tool. It holds the `CREATE TABLE` statements. The database is built by running Alembic, not by the Python model classes. |
| **Dependency injection** | FastAPI's way of giving a function things it needs. `session: SessionDep` in a signature means "FastAPI, please open a database session and hand it to me." `admin: AdminUser` means "and refuse the request entirely if the caller is not an admin." |
| **JWT** | JSON Web Token. A signed string the server gives you at login. You send it back on every request as `Authorization: Bearer <token>`. The server verifies the signature — it does not need to look anything up to know the token is real. |
| **Background task** | Work that runs *after* the HTTP response has already been sent to the browser. Used here so the student's upload returns instantly instead of waiting for notifications to fan out. |

### Frontend words

| Term | What it actually means here |
|---|---|
| **React** | The UI library. You write functions that return HTML-looking code (JSX); React re-runs them when data changes and updates only what moved. |
| **Component** | One of those functions. `UploadPage()` is a component. `RequestCard()` is a component. |
| **State (`useState`)** | A variable React watches. `const [title, setTitle] = useState('')` — when you call `setTitle('x')`, React re-renders the component. |
| **Effect (`useEffect`)** | Code that runs *after* a render, usually because something changed. Used here for debouncing. |
| **TanStack Query (`useQuery`)** | Handles **reading** from the server: it fetches, caches, tracks loading/error state, and refetches when told. You never write `useState` + `useEffect` + `fetch` by hand. |
| **`useMutation`** | The **writing** half of the same library: POST/PATCH/DELETE, with `isPending`, `onSuccess`, `onError` built in. |
| **Query key** | The cache address. `['materials', 'price-suggestion', 'CSE470']` — two components asking with the same key share one network call. |
| **`invalidateQueries`** | "The data I just changed is now stale — refetch it." This is how the moderation queue disappears a row after you approve it. |
| **Debounce** | Waiting until the user stops typing before firing a request. Without it, typing "CSE470" fires six network calls. |
| **TypeScript** | JavaScript with types. `interface Material { title: string }` — the editor catches a typo like `mateiral.titel` before you run anything. |

### The one thing that surprises everyone

**The frontend and backend are two separate programs.** They do not share
memory, variables, or functions. The *only* thing that passes between them is
**JSON over HTTP**. Every arrow in every diagram below that crosses from React
to Python is an HTTP request, and nothing else.

---

## 3. The architecture in one picture

```
┌─────────────────────────────── BROWSER ───────────────────────────────┐
│                                                                       │
│  React components          e.g. UploadPage.tsx, ModerationPage.tsx    │
│         │                                                             │
│         │ calls api.get / api.post / api.upload                       │
│         ▼                                                             │
│  frontend/src/lib/api.ts   ← the ONLY file that calls fetch()         │
│         │                     adds "Authorization: Bearer <jwt>"      │
└─────────┼─────────────────────────────────────────────────────────────┘
          │  HTTP + JSON  (or multipart for the file upload)
          │  http://localhost:8000/api/v1/...
┌─────────▼─────────────────── FASTAPI SERVER ──────────────────────────┐
│                                                                       │
│  app/main.py               builds the app, mounts everything          │
│         │                                                             │
│  app/api/v1/__init__.py    joins all routers under /api/v1            │
│         │                                                             │
│  app/api/v1/*.py           ROUTERS — HTTP in, HTTP out.               │
│    materials.py            Thin. They check permissions, shape        │
│    requests.py             the response, and delegate.                │
│    admin.py                                                           │
│         │                                                             │
│         │ calls                                                       │
│         ▼                                                             │
│  app/services/*.py         SERVICES — the actual business logic.      │
│    storage.py              Anything that touches more than one table  │
│    recommendation.py       lives here, not in a router.               │
│    moderation.py                                                      │
│    matcher.py                                                         │
│    notifier.py                                                        │
│         │                                                             │
│         │ reads/writes via SQLModel                                   │
│         ▼                                                             │
│  app/models/tables.py      Python classes ↔ database tables           │
│         │                                                             │
└─────────┼─────────────────────────────────────────────────────────────┘
          │
┌─────────▼─────────────────────────────────────────────────────────────┐
│  notevault.db      SQLite file at the repo root                       │
│  storage/materials/    the uploaded PDFs                              │
│  storage/previews/     the 3-page preview PDFs                        │
└───────────────────────────────────────────────────────────────────────┘
```

### The two architectural rules

These are stated in the main README and they are worth quoting verbatim in a
viva, because they explain *why* the code is split the way it is:

1. **Routers never write to more than one aggregate.** Anything touching two —
   approving a listing *and* notifying matched requesters — goes through a
   service function that owns the whole unit of work.
2. **Notifications are persisted first, pushed second.** Every notification is a
   database row; the WebSocket push is best-effort on top, so an offline user
   still finds it on next login.

### Where each of the four features sits

| Feature | Frontend | Router | Service | Table |
|---|---|---|---|---|
| FR 1.1 Upload | `features/listings/UploadPage.tsx` | `api/v1/materials.py` | `services/storage.py` | `study_materials` |
| FR 2.1 Request board | `features/community/RequestsPage.tsx` | `api/v1/requests.py` | `services/matcher.py`, `notifier.py` | `requests` |
| FR 3.1 Price suggestion | `features/listings/UploadPage.tsx` | `api/v1/materials.py` | `services/recommendation.py` | `study_materials` (read only) |
| FR 4.1 Moderation | `features/admin/ModerationPage.tsx` | `api/v1/admin.py` | `services/moderation.py` | `study_materials`, `reports` |

---

## 4. How to run it

Run everything from the **main repository**, not from this bundle folder.

```bash
cd /home/mt-labpc/zami/presentation/Cse470_project
```

### First time only

```bash
# 1. Environment — Python 3.11, Node 20, every dependency. Nothing global.
conda env create --file environment.yml
conda activate notevault
export PYTHONPATH="$PWD/backend"

# 2. Configuration
cp .env.example .env
#    then edit .env and set SECRET_KEY to a random string:
#    python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. Storage directories for uploaded files
mkdir -p storage/materials storage/previews storage/qr

# 4. Build the database schema, then fill it with demo data
( cd backend && alembic upgrade head && python scripts/seed.py --reset )

# 5. Build the React frontend
( cd frontend && npm install && npm run build )
```

### Every time after that

```bash
conda activate notevault
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Then open **http://localhost:8000**.

> **`--workers 1` is not optional.** The WebSocket connection registry and the
> APScheduler rental-reminder job are both in-process state. Four workers means
> four schedulers and every reminder sent four times.

### Development mode (two hot-reloading processes)

```bash
( cd backend && uvicorn app.main:app --port 8000 --reload --workers 1 ) &
( cd frontend && npm run dev )
```

Now open **http://localhost:5173** (Vite), not :8000. Vite proxies `/api` and
`/ws` back to port 8000. Use this while developing — a React change appears
without a rebuild.

### Demo accounts

| Role | Email | Password |
|---|---|---|
| **Admin** (for FR 4.1) | `admin@notevault.test` | `admin1234` |
| Student | `student1@notevault.test` | `student1234` |
| Students 2–11 | `student2@notevault.test` … `student11@…` | `student1234` |

### URLs worth knowing

```
http://localhost:8000/           the React app
http://localhost:8000/api/v1/    the REST API
http://localhost:8000/docs       Swagger UI — every endpoint, try them live
http://localhost:8000/health     liveness probe
```

**`/docs` is your best friend in a viva.** It is generated from the code itself,
so it can never be out of date. You can click "Authorize", paste a token, and
call `POST /materials` or `GET /materials/price-suggestion` live in front of the
examiner without touching the UI.

### Starting over from scratch

```bash
rm -f notevault.db notevault.db-wal notevault.db-shm
rm -rf storage/materials/* storage/previews/* storage/qr/*
( cd backend && alembic upgrade head && python scripts/seed.py --reset )
```

---

## 5. How to open and read the database

There is **no database server and no password**. The whole database is a single
file: `notevault.db` at the repository root. This is SQLite.

### Where the path comes from

`backend/app/core/config.py` line 37:

```python
database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'notevault.db'}"
```

and `REPO_ROOT` is computed on line 16 by walking three directories up from
`config.py` (`core` → `app` → `backend` → repo root). So the file always lands
next to `README.md`, no matter which directory you launched from.

### Open it with the command line

```bash
cd /home/mt-labpc/zami/presentation/Cse470_project
sqlite3 notevault.db
```

Then:

```sql
.headers on
.mode column
.tables                          -- list every table
.schema study_materials          -- show the CREATE TABLE for one table
```

### Queries for each of the four features

```sql
-- FR 1.1 — what a fresh upload looks like
SELECT listing_id, title, course_code, course_code_norm,
       listing_type, price, status, page_count, upload_date
FROM study_materials
ORDER BY upload_date DESC LIMIT 5;

-- FR 1.1 — where the files went (paths are relative to the repo root)
SELECT title, file_path, preview_path, file_hash FROM study_materials LIMIT 3;

-- FR 4.1 — the moderation queue, exactly as the admin panel sees it
SELECT listing_id, title, course_code, upload_date
FROM study_materials
WHERE status = 'PENDING'
ORDER BY upload_date ASC;

-- FR 4.1 — the audit trail left behind by a decision
SELECT title, status, moderated_by, moderated_at, moderation_note
FROM study_materials
WHERE moderated_at IS NOT NULL;

-- FR 2.1 — the request board
SELECT request_id, course_code, department, listing_type,
       max_price, status, created_at
FROM requests ORDER BY created_at DESC;

-- FR 2.1 → 4.1 — proof the auto-match fired
SELECT type, message, ref_type, ref_id, is_read, created_at
FROM notifications
WHERE type = 'REQUEST_MATCH'
ORDER BY created_at DESC;

-- FR 3.1 — the sample the price engine is allowed to look at
SELECT course_code_norm, edition, listing_type, price, status
FROM study_materials
WHERE status IN ('APPROVED','RESERVED','COMPLETED')
  AND price IS NOT NULL
  AND listing_type = 'SELL'
ORDER BY course_code_norm;

-- The full-text search index behind FR 1.2 (kept in sync by triggers)
SELECT rowid, title, course_code FROM materials_fts LIMIT 5;
```

Exit with `.quit`.

### Graphical option

Install **DB Browser for SQLite** (`sudo apt install sqlitebrowser`), open
`notevault.db`, and click through the tables. Good for a demo, because you can
put the browser and the app side by side and watch a row appear when you click
Upload.

### Two things to know before you poke at it

- **The database is in WAL mode.** You will see `notevault.db-wal` and
  `notevault.db-shm` next to the main file. That is normal. Delete all three
  together or none of them.
- **Foreign keys are ON in the app but OFF by default in the `sqlite3` CLI.**
  If you insert rows by hand, run `PRAGMA foreign_keys=ON;` first, or you can
  create rows that point at nothing. The app sets this on every connection —
  see `backend/app/core/db.py` line 33.

---

## 6. The life of one request (generic)

Before the four features, here is the path *every* request takes. Once you can
narrate this, each feature is just a variation on it.

**Example: the browser asks for the moderation queue.**

```
 1. USER          clicks "Admin → Moderation"
 2. REACT         ModerationPage mounts. useQuery fires its queryFn.
 3. api.ts        api.get('/admin/moderation/queue', {query:{page:1,page_size:10}})
                  → buildUrl() makes  /api/v1/admin/moderation/queue?page=1&page_size=10
                  → send() attaches   Authorization: Bearer eyJhbGci...
 4. NETWORK       HTTP GET leaves the browser.
 5. main.py       CORS middleware waves it through.
 6. api/v1/__init__.py  routes it to the admin router.
 7. deps.py       Dependencies resolve BEFORE the handler body runs:
                    SessionDep  → get_session() opens an AsyncSession
                    AdminUser   → get_current_user() decodes the JWT,
                                  loads the User row,
                                  require_admin() checks users.role == 'ADMIN'
                                  → 401 if no token, 403 if not an admin.
 8. admin.py      moderation_queue() runs. Two SQL statements:
                    a COUNT(*)  for the total
                    a SELECT    with LIMIT/OFFSET for this page
 9. schemas       Page.build() wraps the rows in {items,total,page,page_size,pages}
                  and Pydantic serialises each row through MaterialRead —
                  which has no file_path field, so paths cannot leak.
10. NETWORK       HTTP 200 + JSON travels back.
11. api.ts        parse() reads the body. Not ok? throw ApiError with the
                  server's own message. Ok? return the parsed object.
12. REACT         useQuery stores it in the cache under its query key,
                  flips isPending→false, and re-renders the component.
13. USER          sees the list of pending listings.
```

### The three ways this path can end early

| Where | What happens | HTTP |
|---|---|---|
| Step 7, no token | `_CREDENTIALS_ERROR` in `deps.py` | **401** |
| Step 7, token but wrong role | `require_admin` raises | **403** |
| Step 8, body fails validation | Pydantic raises before your code | **422** |

### The token refresh loop (in `api.ts`)

Access tokens expire after 30 minutes. When a call comes back **401**:

1. `request()` sees the 401 and that the path is not `/auth/*`.
2. It calls `refreshAccessToken()`, which is **single-flight** — if ten requests
   401 at once, they all wait on *one* refresh call instead of racing.
3. `performRefresh()` POSTs the stored refresh token to `/auth/refresh`.
4. Success → new tokens saved to `localStorage`, and the original request is
   retried **once**.
5. Failure → `endSession()` clears storage and redirects to `/login?next=...`.

The user never sees any of this. This is why you can leave the app open for an
hour and it still works.

---

## 7. Feature 1 — Upload Study Material (FR 1.1)

> *"Students can upload notes, textbooks, or PDFs along with metadata such as
> course code, department, semester, and edition, so materials are easy to
> categorize and find."*

### 7.1 What the user actually sees

1. Click **Upload** in the header.
2. Choose a PDF, PNG or JPG (max 25 MB).
3. Fill in title, description, course code, department, semester, edition.
4. *While they type*, two panels appear on their own:
   - a **duplicate warning** if something similar already exists (FR 1.5)
   - a **suggested price** with the sample size behind it (FR 3.1 — feature 3)
5. Choose SELL / RENT / EXCHANGE / FREE, and a price if it is SELL or RENT.
6. Click **Upload for review**.
7. A toast says *"It is awaiting moderation. Nobody can find it in browse until
   a moderator approves it."* and they land on the listing detail page.

**The listing is not public.** It is created with `status = 'PENDING'`. That is
the handoff into feature 4.

### 7.2 The complete data flow

```
UploadPage.tsx  handleSubmit()          line 255
        │
        │  builds a FormData object (NOT JSON — a file is going with it)
        │    file, title, description, course_code, department,
        │    semester, edition, listing_type, price
        ▼
upload.mutate(form)                     line 222  useMutation
        │
        ▼
api.upload('/materials', form)          lib/api.ts  uploadForm()
        │  → request('POST', '/materials', {form})
        │  → send() sets Authorization but NOT Content-Type
        │    (the browser must set the multipart boundary itself)
        ▼
── HTTP POST /api/v1/materials  (multipart/form-data) ──────────────────
        ▼
create_material()               api/v1/materials.py  line 477
        │
        ├─1─ MaterialCreate(...)         schemas/material.py  line 36
        │      validates BEFORE the file stream is touched
        │      · title 3–200 chars, course_code 2–32, etc.
        │      · _price_matches_type(): SELL/RENT must have a price;
        │        EXCHANGE/FREE must not
        │      failure → _validation_error() → HTTP 422
        │
        ├─2─ storage.save_upload(file)   services/storage.py  line 155
        │      · read the first 1 MiB
        │      · detect_content_type() sniffs MAGIC BYTES:
        │          %PDF-  → application/pdf
        │          \x89PNG → image/png
        │          \xff\xd8\xff → image/jpeg
        │        anything else → HTTP 415
        │      · if the browser DECLARED an allowed type that contradicts
        │        the bytes → HTTP 415 ("really a PDF, not the PNG it claims")
        │      · stream to storage/materials/<uuid>.<ext> in 1 MiB chunks,
        │        SHA-256 hashing in the same pass, aborting past 25 MB (413)
        │      · on ANY exception the half-written file is unlinked
        │      · _page_count()      → pypdf counts pages (None if unreadable)
        │      · generate_preview() → pypdf writes the first 3 pages to
        │                             storage/previews/<uuid>.pdf
        │      returns SavedFile(file_path, file_hash, page_count,
        │                        preview_path, content_type)
        │
        ├─3─ StudyMaterial(...)          models/tables.py  line 64
        │      campus_id comes from current_user.campus_id — there is no
        │      form field for it, because posting on someone else's campus
        │      is meaningless
        │      status = APPROVED if settings.auto_approve_listings
        │               else PENDING          ← normally PENDING
        │
        ├─4─ session.add(listing); await session.commit()
        │      ▶ SQLite AFTER INSERT trigger `study_materials_ai` copies
        │        title/course_code/department/description into materials_fts
        │        so the listing is keyword-searchable immediately
        │
        ├─5─ if status == APPROVED:
        │        background.add_task(_run_matcher, listing_id)   line 340
        │      (only in auto-approve mode; normally the matcher fires from
        │       the admin's approve click instead — see feature 4)
        │
        └─6─ _read_one()                 line 230
               re-reads the row through _material_select(), the ONE join
               every listing endpoint uses, and serialises it as MaterialRead
        ▼
── HTTP 201 + MaterialRead JSON ────────────────────────────────────────
        ▼
upload.onSuccess()                      UploadPage.tsx  line 225
        · queryClient.invalidateQueries(['materials'])  → browse/my-listings
          will refetch next time they are shown
        · toast.success(...)  — the message depends on listing.status
        · navigate(`/materials/${listing.listing_id}`)
```

### 7.3 Every function involved — backend

| File · line | Function | What it does |
|---|---|---|
| `api/v1/materials.py:477` | `create_material` | The endpoint. Orchestrates validate → save → insert → respond. |
| `api/v1/materials.py:296` | `_validation_error` | Multipart form fields dodge FastAPI's automatic body validation, so a `ValidationError` here would surface as a 500. This flattens it into a readable **422** naming the offending field. |
| `api/v1/materials.py:230` | `_read_one` | Re-reads the listing through the canonical join so the response is identical in shape to browse and detail. |
| `api/v1/materials.py:181` | `_material_select` | The one join: listing + uploader name + campus name + review aggregate + "is this bookmarked by me". Built once so a 24-card browse page is 1 query, not 96. |
| `api/v1/materials.py:206` | `_material_read` | Turns one joined row into a `MaterialRead`. Derives `has_preview` from `preview_path` — the path itself never ships. |
| `services/storage.py:155` | `save_upload` | Streams, sniffs, hashes, size-caps, previews. Returns a `SavedFile`. |
| `services/storage.py:142` | `detect_content_type` | Magic-byte sniffing. **This, not the browser's `Content-Type` header, is what decides whether an upload is accepted.** |
| `services/storage.py:226` | `generate_preview` | Slices the first `settings.preview_pages` (3) pages into a separate file. **Never raises** — a listing with no preview is still a good listing. |
| `services/storage.py:215` | `_page_count` | pypdf page count for the card, `None` if the PDF will not open. |
| `services/storage.py:96` | `resolve` | Turns a stored relative path back into an absolute one **and refuses anything outside `storage/`**. One comparison that closes the whole `../../.env` class of bug. |
| `services/storage.py:91` | `_relative` | Stores repo-relative POSIX paths, so moving the checkout does not break every listing. |
| `schemas/material.py:36` | `MaterialCreate` | The validation contract. Not bound directly by the route (multipart), but constructed from the form fields so the rule lives in one place. |
| `schemas/material.py:71` | `_price_matches_type` | Mirrors the database `CHECK` constraint in Python, so the error is a 422 naming `price` instead of an opaque `IntegrityError`. |
| `schemas/material.py:104` | `MaterialRead` | The response shape. **`file_path`, `preview_path` and `file_hash` are deliberately absent.** |
| `core/util.py:29` | `normalize_course_code` | `cse 470`, `CSE-470`, `Cse470` all collapse to `CSE470`. Written into `course_code_norm`. |
| `core/util.py:41` | `normalize_edition` | Same idea for editions: `3rd Ed.` and `3RDED` compare equal. |
| `models/tables.py:64` | `StudyMaterial` | The table class. 20 columns. |

### 7.4 Every function involved — frontend

| File · line | Function | What it does |
|---|---|---|
| `UploadPage.tsx:101` | `UploadPage` | The whole page. 10 pieces of `useState`, 2 queries, 1 mutation. |
| `UploadPage.tsx:186` | `handleFileChange` | Client-side gate: type via `isAcceptedFile`, size via `MAX_FILE_BYTES`. Also auto-fills the title from the filename if the title is still blank. |
| `UploadPage.tsx:97` | `isAcceptedFile` | Checks MIME type; falls back to the file extension when the browser reports no type — *"do not reject what the server would take."* |
| `UploadPage.tsx:236` | `validate` | Returns a `FieldErrors` object. Called only on submit, so the form does not shout at you while you type. |
| `UploadPage.tsx:255` | `handleSubmit` | Builds the `FormData`. Note: `price` is appended **only** when the type is SELL or RENT. |
| `UploadPage.tsx:222` | `upload` (`useMutation`) | `mutationFn` → `api.upload`. `onSuccess` → invalidate + toast + navigate. |
| `UploadPage.tsx:216` | `clearFile` | Resets both the state and the `<input>`'s value, or re-picking the same file fires no `change` event. |
| `UploadPage.tsx:78` | `useDebounced` | Generic 500 ms debounce hook. Feeds both the duplicate check and the price suggestion. |
| `lib/api.ts` | `uploadForm` | Thin wrapper: `request(method, path, {form})`. |
| `lib/api.ts` | `send` | **Deliberately does not set `Content-Type` when a `FormData` is present** — the browser must add its own multipart boundary. |

### 7.5 The four defensive decisions worth quoting

These are the answers to "why did you do it that way?":

1. **Metadata is validated before the stream is touched.** A price on a FREE
   listing is a 422, not a 25 MB orphaned blob in `storage/materials/` that
   nothing will ever reference.
2. **Magic bytes beat the declared type.** `Content-Type` is whatever the
   uploader's browser felt like sending. A file named `notes.pdf` starting with
   `MZ\x90\x00` is a Windows executable and is refused with 415. There is a test
   for exactly this (`test_non_pdf_upload_is_refused`).
3. **The preview is a separate file, not the same file with pages hidden.** If
   you hid pages in the viewer, the full document would still be in the browser's
   network tab. The slice happens at upload time on the server, so the other 200
   pages never leave the machine.
4. **Storage paths never reach the client.** `storage/` is never a static mount.
   Bytes leave only through `GET /materials/{id}/preview` and
   `GET /materials/{id}/file`, and the authorization on the second is what makes
   the first one meaningful.

### 7.6 The status a fresh upload lands in

```
        upload
          │
          ▼
      ┌────────┐  admin approves   ┌──────────┐
      │PENDING │ ────────────────► │ APPROVED │ ← now visible in browse
      └────────┘                   └──────────┘
          │                             │
          │ admin rejects               │ owner edits metadata
          ▼                             │
      ┌──────────┐                      │
      │ REJECTED │ ◄────────────────────┘  (goes back to PENDING —
      └──────────┘                          see below)
```

**Editing an APPROVED listing sends it back to PENDING.** Moderation approved a
*particular* title, course and description; letting the uploader swap them
afterwards would make approval meaningless. `update_material` (line 822) also
clears `moderated_by`/`moderated_at`/`moderation_note`, because a stale
"approved by X" on a queued listing reads as a bug to the next moderator. There
is a test for this: `test_editing_an_approved_listing_returns_it_to_pending`.

---

## 8. Feature 2 — Request Board (FR 2.1)

> *"Students can post a 'wanted' request for a specific book or notes they cannot
> find, inviting other students to respond."*

### 8.1 The idea that makes this feature interesting

A `Request` is **two things at once**, and the second is easy to miss:

1. the **wanted-ad** other students read on the board, and
2. the **forward-looking saved search** that the auto-matcher runs against every
   newly approved listing.

That is why the form collects *structured fields* (course code, department,
edition, listing type, max price, campus) instead of a sentence of free text —
those columns exist precisely so the matcher has something to match on.

**Why a wishlist could not do this job:** a wishlist bookmarks listings that
*already exist*. It can never match a listing created tomorrow. A `Request` has
criteria, not a foreign key, so it can.

### 8.2 What the user sees

- **Your requests** — everything they posted, with a **Close** button.
- **Open requests from other students** — the public board, filterable by course
  code (300 ms debounce) and scoped by the campus filter in the header.
- **Post a request** — a modal. *Only the course code is required.* Every other
  field narrows what counts as a match.

Requests carry one of three statuses:

| Status | Meaning | Who sets it |
|---|---|---|
| `OPEN` | Still watching for a match | Set on creation |
| `MATCHED` | A listing answered it | **Only the auto-matcher.** A user setting this by hand gets a 409. |
| `CLOSED` | The student withdrew it | The owner, via PATCH |

### 8.3 Posting a request — the data flow

```
RequestsPage.tsx  handleSubmit()        line 244
        │  validates course_code present, max_price numeric
        │  orNull() turns '' into null — the server must store "any",
        │  not an empty string
        ▼
createRequest.mutate(payload)           line 203
        ▼
api.post('/requests', payload)          JSON this time, not multipart
        ▼
── HTTP POST /api/v1/requests ──────────────────────────────────────────
        ▼
create_request()                api/v1/requests.py  line 266
        │
        ├─1─ if payload.campus_id: verify the campus exists → 404 if not
        │
        ├─2─ Request(...) built, session.add()
        │      course_code_norm = normalize_course_code(course_code)
        │      listing_type stored as .value (a str), never the enum member
        │      status = OPEN
        │
        ├─3─ demand.record(session, event_type=REQUEST, ...)
        │      Rides in the SAME transaction as the request.
        │      Why: FR 4.4 weights a posted request at 3.0 — the highest of
        │      any demand signal — so losing it would understate exactly the
        │      demand that matters most.
        │      An "any campus" request is attributed to the requester's own
        │      campus, so the trending dashboard is never blank for it.
        │
        ├─4─ await session.commit()   ← request + demand event, one commit
        │
        └─5─ background.add_task(scan_catalogue_for_request, request_id)
        ▼
── HTTP 201 + RequestRead JSON ─────── (returns immediately) ───────────
        ▼
   ... meanwhile, AFTER the response has been sent ...
        ▼
scan_catalogue_for_request()    api/v1/requests.py  line 147
        │  Opens its OWN SessionLocal — the request's session is already
        │  closed by the time a BackgroundTask runs.
        │
        ├─ re-read the Request. Not OPEN any more? return 0.
        ├─ _find_existing_matches()                       line 104
        │     SELECT approved listings WHERE
        │       course_code_norm matches
        │       AND uploader_id != requester_id  (your own upload is not news)
        │       AND department matches       (only if the request set one)
        │       AND listing_type matches     (only if the request set one)
        │       AND campus_id matches        (only if the request set one)
        │       AND (price IS NULL OR price <= max_price)
        │     ORDER BY upload_date DESC LIMIT 20
        │     then _matches_edition() filters in PYTHON, because
        │     "3rd Ed." == "3RDED" is not something SQLite can express
        │     → keep the first 5
        ├─ none found? return 0.
        ├─ notifier.notify(REQUEST_MATCH, ref_type='listing',
        │                  ref_id=best.listing_id, commit=False)
        ├─ request.status = MATCHED
        └─ session.commit()      ← notification + status, one commit
```

### 8.4 Why the catalogue scan exists at all

`services/matcher.py` only fires when a listing is **approved**. So a request
posted for a course that *already* has approved listings would sit silent until
someone happened to upload another one. That is a bad saved search. The
`POST /requests` handler therefore scans the existing catalogue once, in a
background task so the student's click still returns immediately.

### 8.5 Editing a request

`update_request` (line 325) has three rules that are worth knowing:

1. **`MATCHED` cannot be set by hand → 409.** It is the matcher's word for what
   happened. Letting the owner assert it would leave a request advertising a
   match that does not exist.
2. **Changing the criteria of a MATCHED request reopens it.** The old match
   answered the old criteria; change what is wanted and that is no longer true,
   so it goes back to `OPEN` and becomes matchable again. Same reasoning as a
   listing returning to `PENDING` when its metadata is edited.
3. **Changing `course_code` also rewrites `course_code_norm`.** The normalised
   column is what the matcher, trending and duplicate checks all key off. Letting
   the two drift silently disables matching for that request.

### 8.6 Every function involved — backend

| File · line | Function | What it does |
|---|---|---|
| `api/v1/requests.py:209` | `list_requests` | The board. Without an explicit `status` filter, the public board hides `CLOSED` — a withdrawn ad is noise to everyone but its owner, who sees everything via `mine=true`. |
| `api/v1/requests.py:266` | `create_request` | Insert + demand event + background scan. |
| `api/v1/requests.py:325` | `update_request` | Owner-only edit / close. The three rules above. |
| `api/v1/requests.py:375` | `delete_request` | Owner-only. A **real** DELETE — nothing else references a request. |
| `api/v1/requests.py:147` | `scan_catalogue_for_request` | The background catalogue scan. Never raises: an exception in a background task would be logged by Starlette long after the 201 went out. |
| `api/v1/requests.py:104` | `_find_existing_matches` | The mirror image of `matcher.match_listing`'s criteria. Over-fetches 20 because the edition filter runs in Python afterwards. |
| `api/v1/requests.py:92` | `_matches_edition` | Unspecified matches anything; otherwise compare normalised forms. |
| `api/v1/requests.py:73` | `_to_read` | Row + the two joined display names. Names are **joined, not denormalised**, so a student who renames themselves does not leave a stale byline. |
| `api/v1/requests.py:392` | `_get_request_or_404` | Lookup or 404. |
| `api/v1/requests.py:404` | `_assert_owner` | 403 if the request belongs to someone else. |
| `services/matcher.py:86` | `match_listing` | The other direction: an approved listing → every open request it satisfies. **Called from the admin approve endpoint** (feature 4). |
| `services/matcher.py:57` | `_already_notified` | Reads `notifications` back to find who already has an alert for this listing. This is what makes "one notification per user per listing" hold across separate calls and across a process restart. |
| `services/notifier.py:83` | `notify` | Writes the row **first**, then tries the WebSocket push. The push never affects the outcome. |
| `services/demand.py:47` | `record` | One INSERT into `course_demand_events`. No SELECT, no validation round-trip — it runs on every search. |
| `schemas/community.py:32` | `RequestCreate` | Only `course_code` is required. |
| `schemas/community.py:76` | `RequestRead` | The response shape. |
| `models/tables.py:104` | `Request` | The table. Its criteria columns are the whole point. |

### 8.7 Every function involved — frontend

| File · line | Function | What it does |
|---|---|---|
| `RequestsPage.tsx:151` | `RequestsPage` | The page: 3 queries, 2 mutations, 1 modal. |
| `RequestsPage.tsx:84` | `RequestCard` | One row. Renders the criteria as chips; an absent criterion becomes "Any campus" / "Any listing type" so the reader can see it is a wildcard. |
| `RequestsPage.tsx:185` | `boardQuery` | `GET /requests?status=OPEN&course_code=…&campus_id=…&page=…`. Uses `placeholderData: keepPreviousData` so paging does not flash an empty list. |
| `RequestsPage.tsx:191` | `mineQuery` | `GET /requests?mine=true&page_size=50` — your own, every status. |
| `RequestsPage.tsx:197` | `campusesQuery` | `GET /campuses`, cached for an hour (reference data). |
| `RequestsPage.tsx:203` | `createRequest` | `POST /requests` → invalidate `['requests']`, close modal, toast. |
| `RequestsPage.tsx:218` | `closeRequest` | `PATCH /requests/{id}` with `{status:'CLOSED'}`. |
| `RequestsPage.tsx:244` | `handleSubmit` | Client-side validation + `orNull()` conversion. |
| `RequestsPage.tsx:68` | `orNull` | `''` → `null`, so the server stores "any" rather than an empty string. |
| `RequestsPage.tsx:234` | `board` filter | Removes your own requests from the public board — they already have their own section above it. |

---

## 9. Feature 3 — Price Suggestion Engine (FR 3.1)

> *"The system suggests a fair price for a listing based on similar existing
> listings, helping sellers price items reasonably."*

### 9.1 The design goal

This feature is engineered to be **honest rather than confident**. The failure
mode worth designing against is a median presented from a sample of one. So:

- it **always** returns `basis` (which rule produced the number) and
  `sample_size` (how many listings it actually saw);
- when it does not have enough data it says `insufficient_data` and returns
  `null` for all three numbers — *and still reports the sample size*, so the UI
  can explain **why** there is no number.

### 9.2 The fallback ladder

`suggest_price` walks down four rungs and stops at the **first one holding at
least `settings.price_min_sample` (default 3) listings**:

| Rung | `basis` value | The sample | What the UI says |
|---|---|---|---|
| 1 | `course_edition` | Same course **and** same edition | *"Based on listings for this exact course and edition."* |
| 2 | `course` | Same course, any edition | *"Based on listings for this course, across every edition."* |
| 3 | `department` | Same department **and** semester as this course's newest listing | *"…not enough for this exact course, so the sample was widened to the same department."* |
| 4 | `campus` | Every priced listing on the campus | *"…widened to every listing on your campus."* |
| — | `insufficient_data` | Nothing reached 3 | *"Not enough comparable listings to put a number on."* |

Those five sentences live in `BASIS_NOTE` at `UploadPage.tsx:56`. They exist
because **"৳450 based on the department average" is a materially weaker claim
than "৳450 for this exact course"**, and the panel must not let the two read
alike.

### 9.3 What is allowed into the sample

From `recommendation.py:215`, every rung starts from the same base filter:

```python
base = [
    status IN ('APPROVED', 'RESERVED', 'COMPLETED'),   # _PRICE_STATUSES
    upload_date >= now - 180 days,                     # price_lookback_days
    listing_type == the requested type,                # never relaxed
    price IS NOT NULL,
]
if campus_id: base.append(campus_id == ...)            # campus_scoped
```

Three of those are worth defending out loud:

- **`PENDING` is excluded.** A listing still in the moderation queue is not a
  price anybody has agreed to look at, let alone pay. `REJECTED` and `REMOVED`
  are excluded for the obvious reason. There is a test for this:
  `test_pending_listings_are_not_evidence_of_a_going_rate`.
- **`listing_type` is never relaxed.** A rental price and a sale price for the
  same book are different numbers, and averaging them helps nobody. If you ask
  for `FREE` or `EXCHANGE` you get `insufficient_data` immediately, because
  those types carry no price by construction (there is a database `CHECK`).
- **180-day window.** Last year's textbook price is not this year's.

### 9.4 The maths

```python
_suggestion(prices, basis):
    ordered = sorted(prices)
    suggested = _percentile(ordered, 0.50)   # the median
    low       = _percentile(ordered, 0.25)   # first quartile
    high      = _percentile(ordered, 0.75)   # third quartile
```

**Why median, not mean?** One student listing a textbook at ৳50,000 as a joke
would drag a mean upward for everyone. The median ignores it.

**Why the interquartile range?** "Most sit between ৳150 and ৳250" is more useful
to a seller than a single number with no sense of spread.

`_percentile` (line 352) is linear-interpolated — the same method as
`numpy.percentile`'s default, written in eight lines so the project does not take
a NumPy dependency for samples of a few dozen rows.

Worked example, matching the actual test:

```
prices = [100, 200, 300]
position for 0.50 = (3-1) × 0.50 = 1.0 → exactly index 1 → 200.0   (suggested)
position for 0.25 = (3-1) × 0.25 = 0.5 → between 100 and 200 → 150.0  (low)
position for 0.75 = (3-1) × 0.75 = 1.5 → between 200 and 300 → 250.0  (high)
```

### 9.5 The data flow

```
UploadPage.tsx
   user types "CSE470" in the course code field
        │
        │ 500 ms of silence (useDebounced, line 134)
        ▼
suggestionQuery                          line 165
   enabled: priced && course.length >= 2
   queryKey: ['materials','price-suggestion', course, edition, listingType]
        │  ← two components with the same key share ONE network call;
        │    staleTime 5 minutes means re-typing the same code is free
        ▼
api.get('/materials/price-suggestion', {query:{course_code, edition,
                                               listing_type}})
        ▼
── HTTP GET /api/v1/materials/price-suggestion?course_code=CSE470&… ────
        ▼
price_suggestion()               api/v1/materials.py  line 618
        │  campus_id = current_user.campus_id if campus_scoped else None
        ▼
recommendation.suggest_price()   services/recommendation.py  line 215
        │
        ├─ listing_type not in ('SELL','RENT')? → insufficient_data, done
        ├─ normalise the course code and the edition
        ├─ ONE query for all same-course rows (price + edition)
        │    split in Python into course_prices and edition_prices,
        │    because "same edition" is a normalize_edition() comparison
        │    and SQLite has no expression that says so
        ├─ rung 1: len(edition_prices) >= 3 ? → _suggestion(course_edition)
        ├─ rung 2: len(course_prices)  >= 3 ? → _suggestion(course)
        ├─ rung 3: _course_placement() reads the department+semester off
        │          the course's own newest listing, then queries that
        │          neighbourhood → _suggestion(department)
        ├─ rung 4: the whole campus → _suggestion(campus)
        └─ nothing? → _suggestion(widest_sample_seen, insufficient_data)
                       so sample_size is still truthful
        ▼
PriceSuggestion.model_validate(dict)     schemas/material.py  line 205
        ▼
── HTTP 200 {"suggested":200.0,"low":150.0,"high":250.0,
             "sample_size":3,"basis":"course_edition"} ──────────────────
        ▼
UploadPage renders the panel              line 545
   · "৳200 suggested · based on 3 similar listings"
   · "Most sit between ৳150 and ৳250."
   · BASIS_NOTE[basis]  ← always shown
   · an [Apply ৳200] button that calls setPrice()
```

### 9.6 One routing detail worth mentioning

Look at the comment above `price_suggestion` in `materials.py:605`:

> *Declared before `/materials/{listing_id}`: FastAPI matches routes in order,
> so the parameterised route would otherwise swallow "price-suggestion" as an
> id.*

If the route order were reversed, `GET /materials/price-suggestion` would be
interpreted as *"fetch the listing whose id is the string `price-suggestion`"*
and return 404. This is a small thing that examiners like, because it shows you
understand that route matching is ordered.

### 9.7 Every function involved

| File · line | Function | What it does |
|---|---|---|
| `api/v1/materials.py:618` | `price_suggestion` | The endpoint. Accepts `course_code`, `edition`, `listing_type`, `campus_scoped`. |
| `services/recommendation.py:215` | `suggest_price` | The ladder. |
| `services/recommendation.py:326` | `_suggestion` | Builds the payload. Reports `sample_size` even when there is no number. Emits both `suggested` and the `suggested_price` alias so the two type definitions do not need a translation layer. |
| `services/recommendation.py:352` | `_percentile` | Linear-interpolated percentile over a sorted sample. |
| `services/recommendation.py:301` | `_prices` | Small helper: run a filtered SELECT, return the non-null prices as floats. |
| `services/recommendation.py:308` | `_course_placement` | *"Which department and semester is this course taught in?"* — read off the course's newest listing. A CSE470 book is priced like other final-year CSE books. |
| `schemas/material.py:205` | `PriceSuggestion` | The response model. All three numbers are `None` when `basis == 'insufficient_data'`. |
| `core/config.py:60` | `price_min_sample` = 3 | Below this, no number. |
| `core/config.py:61` | `price_lookback_days` = 180 | The window. |
| `UploadPage.tsx:165` | `suggestionQuery` | The `useQuery`, gated on `priced && course.length >= 2`. |
| `UploadPage.tsx:179` | `hasNumber` | `basis !== 'insufficient_data' && typeof suggested === 'number'`. Controls which of the two panels renders. |
| `UploadPage.tsx:56` | `BASIS_NOTE` | The five sentences. |

### 9.8 The same file also holds FR 1.5

`recommendation.py` is the **RecommendationEngine** from the class diagram. It
has two halves: `suggest_price` (FR 3.1, above) and `check_duplicate` (FR 1.5,
owned by the same file but a different requirement). They share the module
because they share the "warn honestly, never block" philosophy and the same
normalisation helpers. The class holds no state, so it lives here as async
functions over a session rather than as a table.

---

## 10. Feature 4 — Admin Moderation Panel (FR 4.1)

> *"Administrators review and approve or reject newly uploaded listings before
> they become publicly visible."*

### 10.1 Why this feature is the keystone

Every upload lands `PENDING`. A `PENDING` listing:

- does **not** appear in browse or search,
- returns **404** — not 403 — to anyone but its uploader and the admins,
- cannot be transacted on.

So nothing in the catalogue is real until an admin clicks Approve. And that
click does more than flip a status: **it fires the auto-match** that notifies
every student with an open request for that course (feature 2).

### 10.2 How "admin" is implemented

There is **no separate admin table**. The Admin generalization from the class
diagram is implemented as **single-table inheritance**: `users.role` is either
`'STUDENT'` or `'ADMIN'`, enforced by a database `CHECK` constraint.

What makes that real is one dependency, in `core/deps.py:69`:

```python
async def require_admin(current_user: CurrentUser) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(403, "This action is restricted to administrators.")
    return current_user

AdminUser = Annotated[User, Depends(require_admin)]
```

Every route in `admin.py` takes `AdminUser` in its signature. The check is a
**dependency, not an `if` in each handler** — which means it is impossible to
add a new admin route and forget the guard.

Note `require_admin` reads `current_user.role` from the **database row**, not
from the `role` claim inside the JWT. That is why the test suite can promote a
user to ADMIN in the database and their existing token immediately works.

**The frontend guard is cosmetic.** `RequireAdmin` in `App.tsx:63` hides the
menu item and redirects non-admins away from `/admin`. Anyone can bypass it by
typing the URL — and it does not matter, because the server refuses them anyway.
Say this out loud in a viva; it shows you know where security actually lives.

### 10.3 The two queues

```
/admin              AdminHomePage    two counters, links to both queues
   ├── /admin/moderation   ModerationPage   PENDING listings, oldest first
   └── /admin/reports      ReportsPage      user-filed reports (FR 3.5)
```

`AdminHomePage` gets its counters in a neat way: it calls each queue endpoint
with `{page:1, page_size:1}` and reads `Page.total` off the envelope. One cheap
query each, and the number **can never disagree** with what the queue pages
themselves show.

**Oldest first is not an aesthetic choice.** A queue sorted newest-first starves
the uploads at the bottom — precisely the failure a moderation panel exists to
prevent.

### 10.4 Approving — the complete data flow

```
ModerationPage.tsx
   admin clicks [Approve] on a card
        ▼
approve.mutate(item)                     line 70
        ▼
api.post(`/admin/materials/${id}/approve`, {note: null})
        ▼
── HTTP POST /api/v1/admin/materials/{id}/approve ──────────────────────
        ▼
   DEPENDENCIES RESOLVE FIRST
     SessionDep → an AsyncSession
     AdminUser  → decode JWT → load User → role must be 'ADMIN'
                  (401 no token / 403 wrong role — the handler never runs)
        ▼
approve_material()               api/v1/admin.py  line 218
        │
        ├─ _load_material_row()                        line 125
        │    ONE query fetching the listing + uploader name + campus name.
        │    The row holds the LIVE StudyMaterial instance, so the same
        │    query serves both the mutation and the response.
        │    Not found → 404.
        │
        └─ moderation.approve_listing()  services/moderation.py  line 52
              │
              ├─1─ _assert_pending()                   line 28
              │      status != PENDING → HTTP 409.
              │      THIS IS THE DOUBLE-CLICK GUARD. Without it, a second
              │      click re-runs the fan-out and every requester on the
              │      course gets a second alert.
              │
              ├─2─ listing.status = APPROVED
              │    _stamp(listing, admin, note)         line 45
              │      moderated_by = admin.user_id
              │      moderated_at = utcnow()
              │      moderation_note = note
              │      ← this is the audit trail behind every decision
              │    session.commit()
              │      ▶ the AFTER UPDATE trigger refreshes materials_fts
              │
              ├─3─ notifier.notify(uploader, MODERATION_RESULT,
              │                    "Your listing '…' was approved and is
              │                     now visible.")
              │
              └─4─ matcher.match_listing(session, listing)   matcher.py:86
                     │  imported INSIDE the function, not at module scope —
                     │  matcher notifies about listings and moderation
                     │  approves them, so a top-level import is a cycle
                     │
                     ├─ guard: status must be APPROVED (not an assert —
                     │    assertions vanish under `python -O`)
                     ├─ SELECT requests WHERE
                     │     status = OPEN
                     │     AND course_code_norm = listing.course_code_norm
                     │     AND requester_id != listing.uploader_id
                     │     AND (department IS NULL OR department = …)
                     │     AND (listing_type IS NULL OR listing_type = …)
                     │     AND (campus_id IS NULL OR campus_id = …)
                     │     AND (max_price IS NULL OR max_price >= price)
                     │        ← NULL means "don't care" and matches everything
                     ├─ _already_notified() reads notifications back, so a
                     │    retry cannot double-alert anyone
                     ├─ for each match:
                     │     _edition_matches()? (Python, not SQL)
                     │     notify(REQUEST_MATCH, ref_type='listing',
                     │            ref_id=listing_id, commit=False)
                     │     request.status = MATCHED
                     └─ ONE commit for the notifications AND the status
                          changes — a requester never sees an alert for a
                          request the board still shows as OPEN
                     returns  notified (an int)
        ▼
ApprovalResult(listing=…, notified=3)    admin.py  line 59
        ▼
── HTTP 200 {"listing": {...}, "notified": 3} ──────────────────────────
        ▼
approve.onSuccess()                      ModerationPage.tsx  line 79
   toast.success("Approved — 3 students notified",
                 "Everyone with an open request for this course has been
                  told the listing is live.")
   invalidateAdmin() → invalidateQueries(['admin'])
                       → the queue refetches, the row disappears,
                         AND the counter on /admin drops by one
                         (both use the ['admin'] key prefix)
```

### 10.5 Why the matcher runs *inline*, not in a background task

This is the most likely "why did you do that?" question on this feature, and
there is a real answer:

> The response carries `notified` — the number of students the fan-out told. A
> background task cannot be counted before it has run. The fan-out is capped
> inside the matcher, so this stays a bounded unit of work.

Without that number, "Approved" and "Approved — 3 students notified" look
identical to the admin, and a fan-out that silently sent nothing would be
indistinguishable from one that worked. That is also exactly what the test
asserts.

### 10.6 Rejecting

```
admin clicks [Reject] → a Modal opens demanding a note
        ▼
submitReject()                   ModerationPage.tsx  line 108
   if (!reason) → setNoteError(...) and STOP. No request is sent.
        ▼
POST /admin/materials/{id}/reject   {note: "Pages 4-9 are unreadable."}
        ▼
reject_material()                admin.py  line 241
        ▼
moderation.reject_listing()      services/moderation.py  line 89
   if not note.strip():  → HTTP 422
        "A rejection note is required -- the uploader is shown this reason."
   _assert_pending()     → 409 if it is not PENDING
   status = REJECTED, _stamp(...)
   notify(uploader, MODERATION_RESULT,
          "Your listing '…' was not approved: <the note>")
```

**Why is the note required in the service and not in the schema?** Because
approve and reject **share one request body** (`ModerationDecision`, which has a
single optional `note` field). Making it required in the schema would either
force a note on approvals too, or need two near-identical models. So the schema
stays permissive and `reject_listing` enforces it with a 422 naming the field.

**Why require it at all?** *"A rejection with no reason is indistinguishable
from the site being broken."* The uploader sees that note verbatim; it is the
only feedback they get.

### 10.7 Three separate functions, not one `moderate(action=...)`

`moderation.py` deliberately has `approve_listing`, `reject_listing` and
`remove_listing` rather than one parameterised function. The reasoning, quoted
from the module docstring:

> Reject demands a note and approve does not, remove is reachable from any
> status while the other two are PENDING-only, and only approve triggers the
> matcher — a single function would be three branches sharing nothing but a
> signature.

`remove_listing` is the takedown path used by the **report queue** (FR 3.5): it
works from any status, because a listing that was fine at review time can turn
out to be fake or copyrighted after it has been approved, reserved or completed.

### 10.8 Report resolution — the one piece of real logic in the router

`resolve_report` (`admin.py:298`) lives in the router rather than a service
because *"resolve the report" and "take the listing down" are two decisions the
admin makes in one click and only the router knows they arrived together.*

Its rules:

| Rule | Why |
|---|---|
| Report must be `OPEN` → else 409 | Reopening a closed report is not supported. |
| `remove_listing: true` + `action: 'DISMISS'` → 422 | Contradictory. Dismissing means no action was needed. Rejected rather than guessed at. |
| `remove_listing` is independent of `action` | A report can be upheld without the listing coming down (a warning, a metadata fix), so the destructive half is opt-in. |
| Already-REMOVED listing does not block resolution | A second report on the same listing is the normal case; the first admin's takedown must not stop this one closing their report. |
| The **reporter is not notified** of the outcome | A reporter who learns the outcome of every report learns who they can get taken down. That is its own abuse vector. |

### 10.9 Every function involved — backend

| File · line | Function | What it does |
|---|---|---|
| `api/v1/admin.py:185` | `moderation_queue` | `GET /admin/moderation/queue`. PENDING only, oldest first, paged. |
| `api/v1/admin.py:218` | `approve_material` | `POST …/approve`. Returns `ApprovalResult{listing, notified}`. |
| `api/v1/admin.py:241` | `reject_material` | `POST …/reject`. Note enforced by the service. |
| `api/v1/admin.py:265` | `list_reports` | `GET /admin/reports?status=…`. Unfiltered returns the whole history — an admin auditing what a colleague dismissed last week needs that. |
| `api/v1/admin.py:298` | `resolve_report` | `POST …/resolve`. The five rules above. |
| `api/v1/admin.py:381` | `admin_stats` | `GET /admin/stats`. Five counts in **one** statement with five scalar subqueries, because this renders on every admin page load and SQLite has a single writer that should not queue behind a dashboard. |
| `api/v1/admin.py:87` | `_material_stmt` | Listing + uploader + campus. **LEFT** joins, not inner: a listing whose uploader row went missing should still be moderatable — the queue is where broken data most needs to be visible. |
| `api/v1/admin.py:125` | `_load_material_row` | One query serving both the mutation and the response. |
| `api/v1/admin.py:145` | `_report_stmt` | Report + listing title + reporter name. A queue of bare ids is unusable. |
| `api/v1/admin.py:59` | `ApprovalResult` | Router-local response model. `notified` is the whole point. |
| `api/v1/admin.py:72` | `AdminStats` | The five dashboard tiles. |
| `services/moderation.py:52` | `approve_listing` | Publish → notify uploader → run matcher. Returns the count. |
| `services/moderation.py:89` | `reject_listing` | Note required (422) → REJECTED → notify. |
| `services/moderation.py:124` | `remove_listing` | Takedown from **any** status. The notice-and-takedown half of the copyright answer. |
| `services/moderation.py:28` | `_assert_pending` | The 409 that stops a double-click re-notifying everyone. |
| `services/moderation.py:45` | `_stamp` | Who decided, when, and why. |
| `services/matcher.py:86` | `match_listing` | Approved listing → open requests → notifications. Returns the count. |
| `services/matcher.py:172` | `match_wishlist_watchers` | A deliberately **weaker** second signal: someone who bookmarked *a different* listing for the same course. Capped at 20 and de-duplicated against the request matches. |
| `services/matcher.py:220` | `run_for_listing` | Both passes in the right order — requests first so the wishlist pass can skip those users. |
| `core/deps.py:69` | `require_admin` | The role check, as a dependency. |
| `schemas/admin.py:17` | `ModerationDecision` | `{note?: string}` — shared by approve and reject. |
| `schemas/admin.py:27` | `ReportResolution` | `{action, remove_listing, note}`. |

### 10.10 Every function involved — frontend

| File · line | Function | What it does |
|---|---|---|
| `ModerationPage.tsx:48` | `ModerationPage` | The queue page. |
| `ModerationPage.tsx:57` | `queueQuery` | `GET /admin/moderation/queue?page=…&page_size=10`. |
| `ModerationPage.tsx:70` | `approve` | The approve mutation. `onSuccess` reads `result.notified` to build the toast. |
| `ModerationPage.tsx:89` | `reject` | The reject mutation. |
| `ModerationPage.tsx:108` | `submitReject` | Blocks an empty note **client-side too**, so the user gets an instant message instead of a round trip. |
| `ModerationPage.tsx:66` | `invalidateAdmin` | `invalidateQueries(['admin'])` — one call refreshes the queue *and* both counters on the home page, because every admin query key starts with `'admin'`. |
| `ModerationPage.tsx:158` | `busy` (per row) | Compares `mutation.variables?.listing_id` to the row's id, so only the card being acted on shows a spinner. |
| `AdminHomePage.tsx:169` | `AdminHomePage` | The landing page with the two counters. |
| `AdminHomePage.tsx:170` | `pendingQuery` | `GET /admin/moderation/queue?page=1&page_size=1` → read `.total`. |
| `AdminHomePage.tsx:176` | `reportsQuery` | `GET /admin/reports?status=OPEN&page=1&page_size=1` → read `.total`. |
| `AdminHomePage.tsx:78` | `CounterCard` | The big-number card. Handles loading / failed / value states. |
| `AdminHomePage.tsx:148` | `MODERATION_NOTES` | Four short explanations rendered on the page — the same four points as section 10.1. |
| `App.tsx:63` | `RequireAdmin` | Route guard. Cosmetic; `require_admin` on the server is the real one. |

### 10.11 Why a hidden listing is a 404, not a 403

`_is_visible` in `materials.py:266`:

> A PENDING or REJECTED listing is between its uploader and the moderators, so
> everyone else gets a 404 rather than a 403 — a 403 would confirm the listing
> exists, which is itself information the caller has no claim to.

The counterpart is `test_a_hidden_listing_is_a_404_not_a_403` in `test_authz.py`.
This is a nice detail to have ready: **403 leaks existence, 404 does not.**

There is a second version of the same hole, closed in `search_materials`
(line 697): `GET /materials?status=PENDING` is refused with **403** for anyone
who is not an admin or the owner of those listings. It is the one search
parameter that could otherwise expose the entire moderation queue to anybody who
guessed at it. Tested by
`test_the_status_filter_cannot_be_pointed_at_the_moderation_queue`.

---

## 11. How the four features connect

### The full loop, end to end

```
   ┌────────────────────────────────────────────────────────────────┐
   │                                                                │
   │  ①  A student posts a WANTED AD                    [FR 2.1]    │
   │     POST /requests {course_code:'CSE470', max_price:600}       │
   │     → requests row, status=OPEN                                │
   │     → demand event written (weight 3.0, feeds FR 4.4)          │
   │     → background: scan the existing catalogue                  │
   │                                                                │
   │  ②  Another student starts an UPLOAD               [FR 1.1]    │
   │     types "CSE470" in the course field                         │
   │           │                                                    │
   │           ├──► GET /materials/check-duplicate      [FR 1.5]    │
   │           │    "2 similar listings already exist" — a WARNING  │
   │           │                                                    │
   │           └──► GET /materials/price-suggestion     [FR 3.1]    │
   │                "৳200 suggested · based on 3 similar listings"  │
   │                "Most sit between ৳150 and ৳250."               │
   │                                                                │
   │  ③  They submit                                    [FR 1.1]    │
   │     POST /materials (multipart)                                │
   │     → magic bytes sniffed, SHA-256 taken, 3-page preview cut   │
   │     → study_materials row, status=PENDING                      │
   │     → FTS trigger indexes it                                   │
   │     → INVISIBLE to everyone but the uploader and admins        │
   │                                                                │
   │  ④  An ADMIN opens the moderation queue            [FR 4.1]    │
   │     GET /admin/moderation/queue   (AdminUser dependency)       │
   │     → the new listing, oldest first                            │
   │                                                                │
   │  ⑤  The admin clicks APPROVE                       [FR 4.1]    │
   │     POST /admin/materials/{id}/approve                         │
   │     → status=APPROVED, moderated_by/at/note stamped            │
   │     → uploader notified (MODERATION_RESULT)                    │
   │     → matcher.match_listing() runs INLINE  ─────────┐          │
   │                                                     │          │
   │  ⑥  The matcher finds ①'s request       [FR 2.1/2.3]│          │
   │     course_code_norm matches, price within budget   │          │
   │     → notification row (REQUEST_MATCH,              │          │
   │        ref_type='listing', ref_id=<the new listing>)│          │
   │     → request.status = OPEN → MATCHED               │          │
   │     → returns notified = 1  ────────────────────────┘          │
   │                                                                │
   │  ⑦  The admin sees "Approved — 1 student notified"             │
   │     The student's bell lights up. Clicking it opens the        │
   │     listing they asked for three days ago.                     │
   │                                                                │
   │  ⑧  The listing is now part of the sample the price            │
   │     suggestion draws from  ────────────────► back to ②         │
   └────────────────────────────────────────────────────────────────┘
```

### The shared spine: `course_code_norm`

All four features key off the **same normalised course code column**, written by
`normalize_course_code` in `core/util.py:29`:

```
"cse 470"  ─┐
"CSE-470"  ─┼──►  "CSE470"  ──►  study_materials.course_code_norm
"Cse470"   ─┘                    requests.course_code_norm
                                 course_demand_events.course_code_norm
```

This single helper is what makes duplicate detection (1.5), price suggestion
(3.1), auto-match (2.3) and trending (4.4) all agree on what "the same course"
means. If it were not there, a request for `cse 470` would never match a listing
for `CSE-470`, and everything downstream would silently half-work. There is a
dedicated test: `test_course_codes_match_across_spacing_and_punctuation`.

### Two ways a request gets matched

Notice there are **two** paths, and they exist for different reasons:

| Path | Trigger | Function | Why |
|---|---|---|---|
| **Forward** | An admin approves a listing | `matcher.match_listing` | The normal case. New material answers old wants. |
| **Backward** | A student posts a request | `requests.scan_catalogue_for_request` | Otherwise a request for a course that already has listings would sit silent until someone happened to upload another one. |

Both write the same `REQUEST_MATCH` notification pointing at the same
`ref_type='listing'`, so the bell menu behaves identically either way — and the
approval-time matcher can see that a pair has already been announced.

---

## 12. Database tables used by these features

The whole schema is defined in
`backend/alembic/versions/0001_initial_schema.py` as **hand-written DDL**, not
autogenerated. Two reasons, both worth quoting:

> SQLModel cannot express CHECK constraints, partial uniqueness or FTS5 virtual
> tables, and those are where the real invariants live. […] The DDL is the
> canonical schema; the SQLModel definitions in `app/models/tables.py` mirror
> it, so the two cannot drift.

### `study_materials` — FR 1.1, FR 3.1, FR 4.1

```sql
CREATE TABLE study_materials (
    listing_id       TEXT PRIMARY KEY,
    uploader_id      TEXT NOT NULL REFERENCES users(user_id),
    campus_id        TEXT NOT NULL REFERENCES campuses(campus_id),
    title            TEXT NOT NULL,
    description      TEXT,
    course_code      TEXT NOT NULL,          -- as typed
    course_code_norm TEXT NOT NULL,          -- 'CSE470' — the join key
    department       TEXT NOT NULL,
    semester         TEXT NOT NULL,
    edition          TEXT,
    listing_type     TEXT NOT NULL
                     CHECK (listing_type IN ('SELL','RENT','EXCHANGE','FREE')),
    price            REAL,
    status           TEXT NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING','APPROVED','REJECTED',
                                       'RESERVED','COMPLETED','REMOVED')),
    file_path        TEXT NOT NULL,          -- never leaves the server
    preview_path     TEXT,                   -- never leaves the server
    page_count       INTEGER,
    file_hash        TEXT NOT NULL,          -- SHA-256, drives FR 1.5
    upload_date      TEXT NOT NULL,
    moderated_by     TEXT REFERENCES users(user_id),   -- FR 4.1 audit trail
    moderated_at     TEXT,                              -- FR 4.1
    moderation_note  TEXT,                              -- FR 4.1
    CHECK ((listing_type IN ('EXCHANGE','FREE') AND price IS NULL)
        OR (listing_type IN ('SELL','RENT')     AND price IS NOT NULL
                                                AND price >= 0))
)
```

**That last CHECK is the database's version of the rule `MaterialCreate`
enforces in Python.** Both exist on purpose: Python catches it as a friendly 422
naming the field, SQLite catches it if anything ever bypasses Python.

### `materials_fts` — the search index behind FR 1.2

```sql
CREATE VIRTUAL TABLE materials_fts USING fts5(
    title, course_code, department, description,
    content='study_materials',    -- external content: stores no copy
    content_rowid='rowid'
)
```

Three triggers (`study_materials_ai`, `_ad`, `_au`) keep it in step on INSERT,
DELETE and UPDATE. **Your upload becomes searchable the instant it commits, with
no application code involved.** Never write to this table by hand — that is how
an external-content FTS5 index gets corrupted.

### `requests` — FR 2.1

```sql
CREATE TABLE requests (
    request_id       TEXT PRIMARY KEY,
    requester_id     TEXT NOT NULL REFERENCES users(user_id),
    course_code      TEXT NOT NULL,
    course_code_norm TEXT NOT NULL,   -- the matcher's join key
    department       TEXT,            -- NULL = "any"    ┐
    edition          TEXT,            -- NULL = "any"    │ the criteria
    listing_type     TEXT CHECK (…),  -- NULL = "any"    │ columns —
    max_price        REAL,            -- NULL = "any"    │ the whole
    campus_id        TEXT REFERENCES campuses(campus_id),┘ point
    description      TEXT,
    status           TEXT NOT NULL DEFAULT 'OPEN'
                     CHECK (status IN ('OPEN','MATCHED','CLOSED')),
    created_at       TEXT NOT NULL
)
```

**Every nullable criterion column means "don't care" and matches everything.**
That is the wildcard semantics the matcher implements with
`or_(Request.department.is_(None), Request.department == listing.department)`.

### `notifications` — how features 2 and 4 talk to the user

```sql
CREATE TABLE notifications (
    notification_id TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(user_id),
    type            TEXT NOT NULL,   -- REQUEST_MATCH | MODERATION_RESULT | …
    message         TEXT NOT NULL,
    ref_type        TEXT,            -- 'listing' | 'transaction' | …
    ref_id          TEXT,            -- what the bell menu navigates to
    is_read         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
)
```

`ref_type` + `ref_id` are what make a notification actionable. A REQUEST_MATCH
points at the **listing**, not the request — the useful next click is opening the
material.

### `reports` — FR 3.5's queue, resolved in FR 4.1's panel

```sql
CREATE TABLE reports (
    report_id       TEXT PRIMARY KEY,
    reporter_id     TEXT NOT NULL REFERENCES users(user_id),
    listing_id      TEXT NOT NULL REFERENCES study_materials(listing_id),
    reason          TEXT NOT NULL CHECK (reason IN
                    ('INAPPROPRIATE','FAKE','COPYRIGHT','SPAM','OTHER')),
    details         TEXT,
    status          TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN','RESOLVED','DISMISSED')),
    created_at      TEXT NOT NULL,
    resolved_by     TEXT REFERENCES users(user_id),
    resolved_at     TEXT,
    resolution_note TEXT,
    UNIQUE (reporter_id, listing_id)   -- one report per person per listing
)
```

### `users` and `campuses` — the supporting cast

```sql
users (user_id, name, email UNIQUE, password_hash,
       role CHECK (role IN ('STUDENT','ADMIN')),   ← FR 4.1's whole basis
       campus_id, rating_avg, rating_count, created_at)

campuses (campus_id, name UNIQUE, location)
```

`users.role` is the discriminator that implements the Admin generalization. One
person, one identity, no second table.

### `course_demand_events` — written by FR 2.1, read by FR 4.4

```sql
course_demand_events (event_id, course_code_norm, campus_id,
                      event_type, created_at)
```

Append-only. `create_request` writes one with `event_type='REQUEST'`, which
carries weight **3.0** in `DEMAND_WEIGHTS` — the highest of any signal, because
*"a student who posts a wanted-request is a far stronger demand signal than one
who scrolls past."*

### No `Relationship()` anywhere — a deliberate choice

From `models/tables.py`:

> Lazy loading across an `AsyncSession` raises `MissingGreenlet` at
> unpredictable moments. Every join in this codebase is an explicit `select()`,
> which is more typing and far fewer 3am bugs.

If you are asked why there are no ORM relationships, that is the answer.

---

## 13. Complete API reference

Every path below is prefixed with **`/api/v1`**. Everything except the
noted anonymous cases requires `Authorization: Bearer <access_token>`.

### FR 1.1 — Upload

| Method | Path | Body | Auth | Returns |
|---|---|---|---|---|
| `POST` | `/materials` | multipart: `file`, `title`, `course_code`, `department`, `semester`, `listing_type`, `description?`, `edition?`, `price?` | student | **201** `MaterialRead` |
| `GET` | `/materials` | — (query params) | optional | `Page<MaterialRead>` |
| `GET` | `/materials/{id}` | — | optional | `MaterialRead` (404 if not visible) |
| `PATCH` | `/materials/{id}` | `MaterialUpdate` | **owner** | `MaterialRead` (status → PENDING) |
| `DELETE` | `/materials/{id}` | — | owner or admin | `MessageResponse` (soft delete → REMOVED) |
| `GET` | `/materials/{id}/preview` | — | any signed-in | PDF stream (inline) |
| `GET` | `/materials/{id}/file` | — | uploader / admin / COMPLETED counterparty | PDF stream (attachment) |

**Upload error codes:** `415` wrong file type · `413` over 25 MB · `422`
metadata invalid (e.g. price on a FREE listing) · `401` no token.

### FR 3.1 — Price suggestion (and FR 1.5, its sibling)

| Method | Path | Query / body | Returns |
|---|---|---|---|
| `GET` | `/materials/price-suggestion` | `course_code` (required), `edition?`, `listing_type?` (default `SELL`), `campus_scoped?` (default `true`) | `PriceSuggestion` |
| `POST` | `/materials/check-duplicate` | `{title, course_code, edition?, file_hash?}` | `DuplicateCheckResponse` |

```jsonc
// PriceSuggestion — the "we have an answer" shape
{ "suggested": 200.0, "low": 150.0, "high": 250.0,
  "sample_size": 3, "basis": "course_edition" }

// PriceSuggestion — the "we do not" shape (note sample_size is still real)
{ "suggested": null, "low": null, "high": null,
  "sample_size": 2, "basis": "insufficient_data" }
```

### FR 2.1 — Request board

| Method | Path | Body / query | Auth | Returns |
|---|---|---|---|---|
| `GET` | `/requests` | `status?`, `course_code?`, `campus_id?`, `mine?`, `page?`, `page_size?` | student | `Page<RequestRead>` |
| `POST` | `/requests` | `RequestCreate` (only `course_code` required) | student | **201** `RequestRead` |
| `PATCH` | `/requests/{id}` | `RequestUpdate` | **owner** | `RequestRead` |
| `DELETE` | `/requests/{id}` | — | **owner** | `MessageResponse` |

**Request-board error codes:** `404` unknown campus_id or request ·
`403` not your request · `409` tried to set `status: MATCHED` by hand.

### FR 4.1 — Admin panel

Every route requires `role = 'ADMIN'`. **401** with no token, **403** with a
student's token.

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/admin/moderation/queue` | `page?`, `page_size?` | `Page<MaterialRead>` (PENDING, oldest first) |
| `POST` | `/admin/materials/{id}/approve` | `ModerationDecision` (optional) | `{listing, notified}` |
| `POST` | `/admin/materials/{id}/reject` | `ModerationDecision` (**note required**) | `MaterialRead` |
| `GET` | `/admin/reports` | `status?`, `page?`, `page_size?` | `Page<ReportRead>` |
| `POST` | `/admin/reports/{id}/resolve` | `{action, remove_listing?, note?}` | `ReportRead` |
| `GET` | `/admin/stats` | — | `AdminStats` |

**Moderation error codes:** `409` listing is not PENDING (double-click guard) ·
`422` reject with no note, or DISMISS + remove_listing together · `404` no such
listing/report.

### The pagination envelope

Every list endpoint in the system returns the same shape, so the frontend writes
one paging component instead of six:

```jsonc
{
  "items": [ /* … */ ],
  "total": 47,        // matching rows in the whole table
  "page": 2,          // 1-indexed
  "page_size": 20,
  "pages": 3          // pre-computed, so the client never has to know
}                     // the ceiling-division rule
```

Built by `Page.build()` in `schemas/common.py:82`.

---

## 14. The tests: how they work and what they prove

### Running them

```bash
conda activate notevault
cd backend

pytest                              # everything
pytest -v                           # with each test name
pytest tests/test_materials.py -v   # one file
pytest tests/test_recommendation.py::test_a_thin_sample_admits_it -v  # one test
pytest -k "price"                   # every test with "price" in the name
```

`pytest.ini` sets `asyncio_mode = auto`, so every `async def test_` runs without
a decorator — *"the whole backend is async, and an opt-in marker on 150 tests is
150 chances to forget one and have it silently pass without ever awaiting."*

### The four fixture decisions in `conftest.py`

These are the most quotable part of the test suite, because each one exists
because the obvious alternative produces **tests that pass while the application
is broken**.

#### 1. A real, migrated SQLite file per test

Not `SQLModel.metadata.create_all()`, and not `:memory:`.

> Every invariant that actually matters — the price/listing-type CHECK,
> `UNIQUE (transaction_id, reviewer_id)`, the FTS5 index and its three sync
> triggers — lives in `alembic/versions/0001_initial_schema.py` and in nothing
> else. A suite built from the SQLModel classes would test a schema the
> application never runs on.

So `db_engine` (line ~250) runs `command.upgrade(config, "head")`
programmatically against a throwaway file in pytest's `tmp_path`, once per test.
**Every test starts from a genuinely empty, genuinely correct database.**

#### 2. Factories drive the real HTTP API

- `make_user` → `POST /auth/register` (which also creates the user's wishlist
  row — a hand-inserted user would have none)
- `make_listing` → a real multipart `POST /materials` with a **genuine PDF**
- `make_transaction` → walks the state machine one `PATCH` at a time
- `make_admin` → registers a student, then promotes `users.role` in the database,
  because registration always mints a STUDENT

Rows poked directly into the database are limited to the two states no endpoint
can produce (an expired QR handoff, a due date in the past) and to reference data
with no write side (campuses).

#### 3. Background work must not escape into the developer's database

This one is subtle and worth understanding.

`materials.py`, `requests.py` and `jobs/rental_reminders.py` each do
`from app.core.db import SessionLocal` at the top of the file. Python binds that
**by value at import time**, so each module holds its *own* reference. Patching
`app.core.db.SessionLocal` alone would leave all three copies still pointing at
the real `notevault.db`.

> Missing one does not fail a test — it writes into the real `notevault.db` and
> the assertions still pass.

So `_isolate_background_sessions` (an `autouse` fixture) walks every module in
`sys.modules` whose name starts with `app.` and rebinds every `SessionLocal` it
finds onto the test's session factory.

#### 4. bcrypt runs at 4 rounds

`hash_password` and `verify_password` stay exactly as production has them; only
the work factor moves. At the default 12 rounds the ~60 accounts this suite
registers cost about 15 seconds of pure key stretching, *"and a suite people skip
because it is slow protects nothing."*

### `build_pdf` — why the tests generate a real PDF

`conftest.py:75` builds a structurally valid PDF by hand, xref offsets and all.
Why not just `b"%PDF- fake"`?

> `storage.save_upload` sniffs magic bytes, `pypdf` counts the pages and slices
> the preview, and `/materials/{id}/preview` streams the result. A stub that
> merely starts with `%PDF-` clears the first gate and then costs every listing
> in the suite its preview — **silently**, because preview generation is
> deliberately best-effort and swallows its own failures.

### `test_materials.py` — FR 1.1

| Test | What it proves |
|---|---|
| `test_upload_lands_pending_and_only_appears_once_approved` | Upload → `status == 'PENDING'`, `has_preview == True`, `page_count == 1`. A second student searching for that course gets **0 results**. Admin approves. Now it is **1 result**. This one test ties FR 1.1 and FR 4.1 together. |
| `test_keyword_search_hits_the_fts_index` | Searching `thermodynamics` finds the right listing; searching the **prefix** `discre` finds "Discrete Mathematics" — proving the query really goes through FTS5 with prefix matching rather than a LIKE on everything. |
| `test_filters_by_normalised_course_code_and_listing_type` | A listing uploaded as `CSE 470` is found by a filter for `cse-470`. Normalisation works end to end. |
| `test_price_and_listing_type_must_agree` | FREE + a price → **422** naming `price`. SELL without a price → **422**. Then asserts **nothing was written** — no orphaned blob, no half-listing. |
| `test_non_pdf_upload_is_refused` | A file *named* `notes.pdf`, *declared* as `application/pdf`, whose bytes start with `MZ\x90\x00` → **415**. The bytes decide. |
| `test_preview_is_open_to_any_signed_in_student` | Any signed-in student gets the preview; it is a genuinely **shorter file** than the full download; an anonymous caller gets **401**. |
| `test_full_file_is_the_uploaders_until_a_transaction_completes` | A stranger gets **403** with a message naming what they would need to be. The uploader gets **200** with `Content-Disposition: attachment`. |
| `test_editing_an_approved_listing_returns_it_to_pending` | Editing an APPROVED listing → `status` back to `PENDING`, `moderated_at` and `moderation_note` **cleared**, and the listing vanishes from other students' search results again. |

### `test_recommendation.py` — FR 3.1 (and 1.5)

> Both halves fail in the same direction if they are written naively — they get
> **confident**. So the tests here are about the edges rather than the happy path.

| Test | What it proves |
|---|---|
| `test_a_thin_sample_admits_it` | Two listings at 400 and 600 → `basis == 'insufficient_data'`, all three numbers `null`, **and `sample_size == 2`**. It reports what it saw even when it refuses to average it. |
| `test_three_comparable_listings_produce_a_median_and_a_range` | 100/200/300 → `suggested 200.0`, `low 150.0`, `high 250.0`, `basis 'course_edition'`. Asked with `course_code='cse 470'` and `edition='1ST EDITION'` to prove both sides normalise. |
| `test_pending_listings_are_not_evidence_of_a_going_rate` | Three PENDING listings at 100/200/300 → `insufficient_data` with `sample_size == 0`. An unmoderated asking price is not a market price. |
| `test_free_listings_have_nothing_to_suggest` | `listing_type='FREE'` → `insufficient_data` immediately. Nothing to suggest and nothing to apologise for. |
| `test_identical_file_hash_is_the_strongest_warning` | (FR 1.5) The same bytes under a *different title* and a *different course* still surface, with `similarity == 1.0` and `reason == "identical file"`. |
| `test_the_similarity_threshold_decides_the_boundary` | Moves `settings.duplicate_title_threshold` rather than the titles, proving the comparison is `score < threshold` — so the threshold value itself is still a match. |

### `test_matcher.py` — FR 2.1 / 2.3

> A matcher that notifies everybody is indistinguishable from one that works,
> right up to the moment somebody counts the notifications — so every test here
> asserts **three things together**: the count the admin is shown, the
> recipient's bell, and the status the request board now displays.

| Test | What it proves |
|---|---|
| `test_approval_notifies_the_holder_of_a_matching_open_request` | The happy path. `notified == 1`; the notification has `ref_type='listing'` and the right `ref_id`; the message **names the listing title**; the request flips to `MATCHED`. |
| `test_a_pending_listing_notifies_nobody` | Approval is the trigger, not creation. And it proves *why*: the test then fetches the PENDING listing as the requester and gets a **404**. Notifying early would send people to a page they cannot open. |
| `test_the_uploader_is_not_matched_to_their_own_listing` | `notified == 0` and no REQUEST_MATCH — but the uploader's `MODERATION_RESULT` **does** arrive, proving the empty list is a decision the matcher made and not a notifier that is down. |
| `test_a_request_that_does_not_match_stays_open_and_silent` | **Parameterised over five mismatches** — wrong course, wrong department, wrong listing type, under budget, wrong campus. Each sets exactly one criterion and leaves the rest NULL, so any of them firing means a filter was dropped. |
| `test_null_criteria_are_wildcards` | A request naming *only* a course matches a listing that contradicts what all four optional criteria would have said. Proves NULL really means "any". |
| `test_course_codes_match_across_spacing_and_punctuation` | `cse 220` request matches `CSE-220` listing. The strings the two students typed never meet — both sides are stored normalised. |
| `test_a_closed_request_is_not_matched` | A closed request stays closed and silent. |
| `test_a_request_already_matched_is_not_matched_again` | One want, one alert. A **second, different** listing is approved and `notified == 0`, so per-listing de-duplication cannot be what saved it — the only thing standing between the student and a second alert is their request no longer being OPEN. |
| `test_approving_the_same_listing_twice_does_not_re_notify` | The double-click guard. Second approve → **409**, and the notification count stays at 1. |

### `test_authz.py` — FR 4.1's security

A single parameterised table of `(role, method, path, body, expected_status)`
covering four kinds of caller against every route where getting the answer wrong
leaks something.

Two conventions the expectations encode:

> **401 versus 403 is not cosmetic.** No credentials at all is 401 ("sign in");
> credentials that are simply not enough is 403 ("you, specifically, may not"). A
> route that answers 403 to an anonymous caller has told them the resource exists
> before asking who they are.

The moderation rows read:

```
anonymous  GET  /admin/moderation/queue          → 401
stranger   GET  /admin/moderation/queue          → 403
admin      GET  /admin/moderation/queue          → 200
anonymous  POST /admin/materials/{id}/approve    → 401
stranger   POST /admin/materials/{id}/approve    → 403
admin      POST /admin/materials/{id}/approve    → 200
stranger   POST /admin/reports/{id}/resolve      → 403   ← the REPORTER
                                                            gets no say
```

Plus two standalone tests:

- `test_a_hidden_listing_is_a_404_not_a_403` — a PENDING listing 404s for a
  stranger while an APPROVED one 200s.
- `test_the_status_filter_cannot_be_pointed_at_the_moderation_queue` —
  `GET /materials?status=PENDING` is **403** for a stranger, **403** even if
  they pass someone else's `uploader_id`, and **200** only for the owner asking
  about their own.

### What is *not* tested, and why that is fine to say

There are no frontend unit tests. `npm run build` runs the TypeScript compiler,
so type errors are caught at build time, and the backend suite exercises every
endpoint the UI calls. If asked, say that plainly rather than inventing coverage.

---

## 15. Function index — backend

Grouped by file, in the order they appear. **Bold** = the entry point you would
show an examiner first.

### `app/api/v1/materials.py` — FR 1.1, 1.2, 1.4, 1.5, 3.1

```
_bookmark_flag(viewer)          158  "is this in MY wishlist" as a correlated
                                     subquery, or a constant 0 for anonymous
_material_select(viewer)        181  THE join every listing endpoint reads from
_material_read(row)             206  one joined row → MaterialRead
_read_one(session, id, viewer)  230  re-read through that join, 404 if gone
_get_listing(session, id)       252  plain lookup or 404
_is_visible(listing, viewer)    266  public? owner? admin? → the 404 rule
_assert_visible(...)            280  raises the 404
_assert_owner(...)              288  raises the 403
_validation_error(exc)          296  ValidationError → HTTP 422 (multipart)
_record_demand(...)             317  background: one demand event, own session
_run_matcher(listing_id)        340  background: auto-match, own session
_demand_course_code(q, code)    362  is this string a statement about a COURSE?
_fts_expression(raw)            383  user text → a safe FTS5 MATCH string
_like_condition(raw)            396  the fallback when FTS has nothing to use
_keyword_condition(session,raw) 409  run MATCH, feed rowids back as an IN clause
_order_by(sort)                 442  sort clauses, always closing on a unique col
create_material(...)            477  ★ FR 1.1 — POST /materials
check_duplicate(...)            565    FR 1.5 — POST /materials/check-duplicate
price_suggestion(...)           618  ★ FR 3.1 — GET /materials/price-suggestion
search_materials(...)           655    FR 1.2 — GET /materials
get_material(...)               792    GET /materials/{id}
update_material(...)            822    PATCH — sends an APPROVED listing back
                                       to PENDING
delete_material(...)            904    DELETE — soft, to REMOVED
_file_response(...)             959    serve a blob addressed by a DB column
_safe_filename(title)           982    a download name from the title, never
                                       from a stored path
get_preview(...)                993    FR 1.4 — any signed-in student
get_file(...)                  1024    uploader / admin / COMPLETED counterparty
list_material_reviews(...)     1091    FR 2.5 read side
```

### `app/api/v1/requests.py` — FR 2.1

```
_to_read(request, name, campus)  73  row + two joined display names
_matches_edition(a, b)           92  unspecified matches anything
_find_existing_matches(s, req)  104  approved listings that already satisfy it
scan_catalogue_for_request(id)  147  ★ the background catalogue scan
list_requests(...)              209  ★ GET /requests — the board
create_request(...)             266  ★ POST /requests
update_request(...)             325    PATCH — edit or close
delete_request(...)             375    DELETE — a real delete
_get_request_or_404(...)        392
_assert_owner(request, user)    404
_campus_name(session, request)  412
```

### `app/api/v1/admin.py` — FR 4.1

```
class ApprovalResult              59  {listing, notified} — `notified` is the
                                      whole point of the endpoint
class AdminStats                  72  the five dashboard tiles
_material_stmt()                  87  listing + uploader + campus (LEFT joins)
_material_read(row)              107  row → MaterialRead
_load_material_row(session, id)  125  one query for the mutation AND the response
_report_stmt()                   145  report + listing title + reporter name
_report_read(row)                164
_count(session, stmt)            174
moderation_queue(...)            185  ★ GET /admin/moderation/queue
approve_material(...)            218  ★ POST /admin/materials/{id}/approve
reject_material(...)             241  ★ POST /admin/materials/{id}/reject
list_reports(...)                265    GET /admin/reports
resolve_report(...)              298    POST /admin/reports/{id}/resolve
admin_stats(...)                 381    GET /admin/stats — 5 counts, 1 statement
```

### `app/services/storage.py` — FR 1.1, 1.4

```
class SavedFile                   70  file_path, file_hash, page_count,
                                      preview_path, content_type
_relative(path)                   91  absolute → repo-relative POSIX
resolve(relative_path)            96  ★ relative → absolute, REFUSING to escape
                                      storage/
delete_stored_file(path)         113  best-effort removal
detect_content_type(head)        142  ★ magic-byte sniffing
save_upload(upload)              155  ★ stream + hash + size-cap + preview
_page_count(source, type)        215  pypdf page count, None if unreadable
generate_preview(source, type)   226  ★ slice the first 3 pages. Never raises.
```

### `app/services/recommendation.py` — FR 1.5, 3.1

```
check_duplicate(...)              87    FR 1.5 — up to 5 candidates, never raises
_score(material, ...)            178    (similarity, reason, kind) or None
suggest_price(...)               215  ★ FR 3.1 — the fallback ladder
_prices(session, *conditions)    301    filtered SELECT → list[float]
_course_placement(session, code) 308    which department/semester is this course?
_suggestion(prices, basis)       326  ★ build the payload; sample_size always real
_percentile(ordered, fraction)   352  ★ linear-interpolated percentile
```

### `app/services/moderation.py` — FR 4.1

```
_assert_pending(listing, verb)    28  ★ the 409 double-click guard
_stamp(listing, admin, note)      45  ★ who decided, when, why
approve_listing(...)              52  ★ publish → notify → match. Returns count.
reject_listing(...)               89  ★ note required (422) → REJECTED → notify
remove_listing(...)              124    takedown from ANY status
```

### `app/services/matcher.py` — FR 2.3 (the engine behind 2.1 and 4.1)

```
_already_notified(session, id)    57  read notifications back → a set of user ids
_edition_matches(a, b)            72  normalised comparison, in Python
match_listing(session, listing)   86  ★ approved listing → open requests
match_wishlist_watchers(...)     172    the weaker second signal, capped at 20
run_for_listing(session, listing)220    both passes, requests first
```

### `app/services/notifier.py`

```
_text(value)                      40  store the enum's .value, never str(member)
_frame(notification)              50  the WebSocket frame
_push(notification)               71  best effort. Never raises.
notify(...)                       83  ★ row first, push second
notify_many(...)                 119    same body, several recipients, one flush
unread_count(session, user_id)   166    the bell badge
mark_read(...)                   176    404 if gone, 403 if someone else's
mark_all_read(session, user_id)  208    one UPDATE
```

### `app/services/demand.py` — FR 4.4's write side

```
record(session, ...)              47  ★ one INSERT. No SELECT, no validation.
record_many(session, codes, ...)  90    one event per distinct course code
```

### `app/core/`

```
config.py  Settings                   every tunable, each with a working default
config.py  settings.repo_root         where storage/ and notevault.db live
db.py      _set_sqlite_pragmas    33  foreign_keys=ON, WAL, busy_timeout
db.py      get_session            50  the FastAPI dependency, one per request
deps.py    load_user_from_token   29  shared by HTTP and the WebSocket handshake
deps.py    get_current_user       47  → CurrentUser
deps.py    require_admin          69  ★ → AdminUser. FR 4.1's real guard.
deps.py    get_optional_user      89  → OptionalUser (personalise, don't require)
security.py hash_password / verify    bcrypt, used directly (not passlib)
security.py create_access_token       30-minute JWT
security.py decode_token              raises on expiry, signature or wrong kind
util.py    new_id()               15  uuid4().hex
util.py    utcnow()               20  naive UTC — the single source of "now"
util.py    normalize_course_code  29  ★ the spine of all four features
util.py    normalize_edition      41
```

---

## 16. Function index — frontend

### `src/lib/api.ts` — the only file that calls `fetch`

```
class ApiError                     status, detail (already unwrapped from
                                   FastAPI's {"detail": …}), isNetworkError
extractDetail(status, body, fb)    flattens FastAPI's 422 array into a sentence
getToken / getRefreshToken         read localStorage (try/catch — private mode)
setTokens / clearTokens            write / wipe
buildQuery(query)                  drops undefined/null/'', repeats keys for arrays
buildUrl(path, query)              API_BASE + '/api/v1' + path + '?…'
send(method, path, opts, token)    the raw fetch. Sets Content-Type for JSON,
                                   NEVER for FormData.
parse<T>(res)                      204 / empty handling, then ok? parse : throw
performRefresh()                   POST /auth/refresh
refreshAccessToken()               ★ single-flight — all concurrent 401s share it
endSession()                       clear + redirect to /login?next=…
request<T>(method, path, opts)     ★ the core: 401 → refresh → retry once
uploadForm<T>(path, form, opts)    ★ multipart — used by FR 1.1
requestBlob(path, opts)            binary (the CSV/PDF export, previews)
api.get / post / patch / put / del the ergonomic surface
wsUrl(path, token)                 ws:// URL with the JWT in the query string
                                   (browsers cannot set headers on a WS upgrade)
fileUrl(path, query)               absolute URL for a streamed file
```

### `src/features/listings/UploadPage.tsx` — FR 1.1 + FR 3.1 + FR 1.5

```
BASIS_NOTE                    56  the five price-basis sentences
useDebounced<T>(value, delay) 78  ★ the 500 ms hook feeding both live panels
similarityPercent(similarity) 92  ×100 exactly once — "0.92% match" reads as
                                  the OPPOSITE of what the number means
isAcceptedFile(file)          97  MIME, falling back to the extension
UploadPage()                 101  ★ the page
  duplicateQuery             139    FR 1.5 — POST /materials/check-duplicate
  suggestionQuery            165  ★ FR 3.1 — GET /materials/price-suggestion
  handleFileChange           186    type + size gate, auto-fills the title
  clearFile                  216    resets state AND the input's value
  upload (useMutation)       222  ★ FR 1.1 — api.upload('/materials', form)
  validate                   236    returns FieldErrors
  handleSubmit               255  ★ builds the FormData
```

Two effects worth noting:

- line 118 — when the listing type stops being priced, the price field is
  **cleared as well as hidden**, or the submit would smuggle a value through into
  a `CHECK` violation.
- line 158 — a fresh set of duplicate warnings **resets the acknowledgement**.
  An "I checked" from before the course code changed says nothing about *these*
  listings.

### `src/features/community/RequestsPage.tsx` — FR 2.1

```
orNull(value)                 68  '' → null, so the server stores "any"
RequestCard({request, …})     84  one row; absent criteria render as
                                  "Any campus" / "Any listing type"
RequestsPage()               151  ★ the page
  (debounce effect)          171    300 ms on the course-code filter
  boardParams (useMemo)      177    the query object, memoised so the query key
                                    is stable
  boardQuery                 185  ★ GET /requests?status=OPEN&…
  mineQuery                  191    GET /requests?mine=true
  campusesQuery              197    GET /campuses, 1-hour staleTime
  createRequest              203  ★ POST /requests
  closeRequest               218    PATCH /requests/{id} {status:'CLOSED'}
  patch(next)                239    merge form state and clear errors
  handleSubmit               244  ★ validate + orNull + mutate
  openModal                  272    pre-fills campus from the header filter
  board (filtered)           234    hides your own requests from the public list
```

### `src/features/admin/ModerationPage.tsx` — FR 4.1

```
Meta({label, value})          38  one <dt>/<dd> pair on the card
ModerationPage()              48  ★ the page
  queueQuery                  57  ★ GET /admin/moderation/queue
  invalidateAdmin             66  ★ invalidateQueries(['admin']) — refreshes
                                   the queue AND both home-page counters
  approve                     70  ★ POST …/approve; the toast reads
                                   result.notified
  reject                      89    POST …/reject
  closeReject                102    reset the modal
  submitReject               108  ★ blocks an empty note client-side too
  busy (per row)             158    only the acted-on card spins
```

### `src/features/admin/AdminHomePage.tsx` — FR 4.1

```
COUNT_QUERY                   18  {page:1, page_size:1} — ask for one row,
                                  read Page.total
adminKeys                     22  shared ['admin', …] prefix
TONES / ICONS             35 / 47  Tailwind classes held as literals so the
                                  scanner sees them
CounterCard({...})            78  the big-number card
MODERATION_NOTES             148  the four explanations shown on the page
AdminHomePage()              169  ★ the page
  pendingQuery               170    → total pending listings
  reportsQuery               176    → total open reports
```

### `src/App.tsx` — routing

```
RequireAuth({children})       47  waits for the initial session check rather
                                  than bouncing a signed-in user on refresh
RequireAdmin()                63  ★ hides /admin from non-admins. COSMETIC —
                                  require_admin on the server is the real guard.
App()                         71  every route. Each page is React.lazy'd, so
                                  the login screen does not ship the admin panel.
```

Relevant routes:

```
/upload                → UploadPage        FR 1.1 + 3.1
/requests              → RequestsPage      FR 2.1
/admin                 → AdminHomePage     FR 4.1  ┐
/admin/moderation      → ModerationPage    FR 4.1  ├ inside <RequireAdmin/>
/admin/reports         → ReportsPage       FR 4.1  ┘
```

### `src/lib/types.ts` — the wire contract

Every backend Pydantic schema has a mirror TypeScript interface here. The ones
for these four features:

```
Material              209  ← MaterialRead      (FR 1.1, 4.1)
MaterialCreate        238  ← MaterialCreate    (FR 1.1)
MaterialUpdate        250  ← MaterialUpdate
DuplicateCheckRequest 262  ← DuplicateCheckRequest  (FR 1.5)
DuplicateCandidate    270  ← DuplicateCandidate     "0.0-1.0, NOT a percentage"
PriceSuggestion       292  ← PriceSuggestion   (FR 3.1)
PriceBasis            301  ← the five ladder rungs
RequestRead           313  ← RequestRead       (FR 2.1)
RequestCreate         330  ← RequestCreate
RequestUpdate         340  ← RequestUpdate
ModerationDecision    544  ← ModerationDecision (FR 4.1)
ApprovalResult        556  ← ApprovalResult     {listing, notified}
AdminStats            563  ← AdminStats
Page<T>               115  ← Page[T]            the pagination envelope
```

**These are hand-maintained, not generated.** If you change a Pydantic model,
change the interface here too — TypeScript will then point at every component
that needs updating.

---

## 17. Viva questions and answers

### General

**Q: Walk me through your features.**
Use the loop from section 11: a student posts a wanted-ad (2.1); another student
uploads material and is shown a price suggestion while typing (1.1 + 3.1); the
upload lands PENDING and is invisible; an admin approves it (4.1); approval fires
the matcher, which notifies the first student. Four features, one story.

**Q: Why is the code split into routers and services?**
Routers handle HTTP: permissions, status codes, response shape. Services own
business logic. The rule is *"routers never write to more than one aggregate."*
Approving a listing touches three — the listing, the uploader's notification, and
every matched requester's notification and request status — so it lives in
`services/moderation.py`, not in the router.

**Q: Why async?**
Every endpoint waits on I/O — the database, the filesystem. `async`/`await` lets
one process serve other requests during that wait instead of blocking a thread.
It matters most for the upload endpoint, which streams a 25 MB file.

**Q: Why SQLite and not MySQL/PostgreSQL?**
The SRS specifies local deployment and local file storage. SQLite is one file,
no server, no credentials — which makes the whole project clone-and-run. It is
in WAL mode so readers do not block the single writer. The honest limitation:
SQLite has **one writer**, which is fine at demo scale but would want PostgreSQL
for hundreds of concurrent users. (The SRS also listed Beanie/Motor as the ODM —
those are MongoDB drivers and cannot open a SQLite file, so the ODM row is
implemented as SQLModel + Alembic.)

### FR 1.1 — Upload

**Q: How do you stop someone uploading a virus disguised as a PDF?**
Three layers, and be honest about the fourth. (1) The declared `Content-Type` is
ignored in favour of **magic-byte sniffing** — only `%PDF-`, the PNG signature
and the JPEG signature are accepted, everything else is 415. (2) If the browser
declares an allowed type that **contradicts** the bytes, that is also 415. (3)
The stored filename and extension come from what was **sniffed**, never from
`upload.filename`, so `notes.pdf.exe` cannot survive the round trip. (4) The
honest limitation: **there is no virus scanning**. That is a stated known
limitation, not an oversight.

**Q: What if someone uploads a 5 GB file?**
The stream is written in 1 MiB chunks and the running total is checked on every
chunk, so it aborts with **413** partway through rather than writing 5 GB out to
measure it. And the `except BaseException` block unlinks the half-written file,
because a blob no row references is worse than no blob at all.

**Q: Why is the preview a separate file?**
If you hid pages in the viewer, the full document would still be in the browser's
network tab. Slicing server-side at upload time means the other pages never leave
the machine. That is what makes `GET /materials/{id}/file` — which is separately
authorized to the uploader, an admin, or the counterparty of a **COMPLETED**
transaction — worth having.

**Q: What if the PDF is encrypted and the preview fails?**
`generate_preview` **never raises**. It logs, deletes the partial target, and
returns `None`. The listing is created with `preview_path = NULL` and
`has_preview = false`; the UI just does not offer a preview button. A preview
failure must never cost someone an upload that is already on disk.

**Q: Why is `campus_id` not a form field?**
A listing belongs to the campus of whoever posted it. There is no field for it
because posting on someone else's campus is meaningless. It is read from
`current_user.campus_id`.

### FR 2.1 — Request board

**Q: What is the difference between a request and a wishlist?**
The single best answer in this whole guide. A **wishlist bookmarks listings that
already exist** — it is backward-looking and can never match a listing created
tomorrow. A **request is a forward-looking saved search**: it stores *criteria*
(course, department, edition, type, budget, campus), which is exactly what the
matcher needs to compare a new listing against. That is why the request table has
criteria columns and the wishlist table has a foreign key.

**Q: Why can a user not set status to MATCHED?**
MATCHED is the matcher's word for what actually happened. Letting the owner
assert it would leave a request advertising a match that does not exist. It is a
**409**.

**Q: What happens if I edit a request that was already matched?**
It goes back to OPEN. The old match answered the old criteria; change what is
wanted and that is no longer true. Same reasoning as an edited listing returning
to PENDING.

**Q: Why does posting a request scan the existing catalogue?**
Because the matcher only fires on **approval**. Without the scan, a request for a
course that already has approved listings would sit silent until someone happened
to upload another one — a saved search that ignores today's catalogue is a bad
saved search. It runs in a background task so the student's click still returns
instantly.

**Q: How do you avoid notifying the same person twice?**
Two mechanisms, deliberately independent. Per-listing: `_already_notified` reads
the `notifications` table back and skips anyone who already has an alert for that
listing — which holds even across a process restart or a retried task.
Per-request: a matched request moves to MATCHED and is no longer OPEN, so it
cannot fire for a second listing. `test_a_request_already_matched_is_not_matched_again`
proves the second one is doing real work by approving a *different* listing.

### FR 3.1 — Price suggestion

**Q: How does the price suggestion work?**
A fallback ladder over comparable listings: course+edition → course →
department+semester → whole campus. It stops at the first rung holding at least 3
listings, and returns the **median** plus the **interquartile range**. It always
reports which rung answered (`basis`) and how many listings it saw
(`sample_size`).

**Q: Why median rather than mean?**
One joke listing at ৳50,000 would drag a mean upward for everyone. The median
ignores it.

**Q: What if there are no comparable listings?**
It returns `basis: "insufficient_data"` with all three numbers `null` — **and
still reports the real sample size**, so the UI can say *"Only 2 listings matched
— too few to average honestly"* rather than showing a blank box. That honesty is
the design goal: the failure mode worth engineering against is a median presented
from a sample of one.

**Q: Why exclude PENDING listings from the sample?**
A listing still in the moderation queue is not a price anybody has agreed to look
at, let alone pay. Only APPROVED, RESERVED and COMPLETED count. There is a test:
`test_pending_listings_are_not_evidence_of_a_going_rate`.

**Q: Why not relax `listing_type` when the sample is thin?**
A rental price and a sale price for the same book are different numbers.
Averaging them helps nobody. The ladder widens *geography* and *specificity*, but
never the type.

**Q: Is it machine learning?**
No, and say so plainly. It is a documented statistical fallback with an explicit
minimum sample size and a stated basis. That is a feature, not a shortcoming — a
model that cannot explain its number is worse than a median that can.

### FR 4.1 — Admin moderation

**Q: How do you know someone is an admin?**
`users.role` is `'STUDENT'` or `'ADMIN'`, enforced by a database CHECK. The
`require_admin` **dependency** reads that column and raises 403 otherwise. Every
route in `admin.py` takes `AdminUser` in its signature, so the check is
structural — you cannot add a route and forget it.

**Q: Why not a separate Admin table?**
The class diagram shows an Admin generalization; it is implemented as
single-table inheritance with `users.role` as the discriminator. One person, one
identity. A separate table would mean two rows for the same human and a join on
every request.

**Q: Can I bypass the admin panel by typing the URL?**
You can bypass the *frontend* guard — `RequireAdmin` in `App.tsx` only hides the
UI. You cannot bypass the server: every admin endpoint returns 403. That is the
point of putting the check in a dependency rather than in the router body.
`test_authz.py` asserts it row by row.

**Q: What happens if the admin double-clicks Approve?**
The second click gets a **409**. `_assert_pending` refuses to approve a listing
that is not PENDING. Without it, the second click would re-run the whole fan-out
and every requester on the course would get a second alert.
`test_approving_the_same_listing_twice_does_not_re_notify` asserts both the 409
**and** that the notification count stays at 1.

**Q: Why does approving return a number?**
Because the matcher runs inline and the response carries `notified`, so the UI
can say *"Approved — 3 students notified."* A background task cannot be counted
before it has run. Without that number, a fan-out that silently sent nothing
would look identical to one that worked.

**Q: Why must a rejection have a note?**
*"A rejection with no reason is indistinguishable from the site being broken."*
The uploader is shown that note verbatim; it is the only feedback they get. It is
enforced in `reject_listing` (a **422**) rather than in the schema, because
approve and reject share one request body.

**Q: Why does a hidden listing return 404 and not 403?**
403 would confirm the listing exists, which is itself information the caller has
no claim to. A PENDING listing is between its uploader and the moderators, so to
everyone else it simply does not exist.

**Q: Could a student see the moderation queue through the search endpoint?**
That was the hole, and it is closed. `GET /materials?status=PENDING` returns
**403** unless you are an admin or asking about your own `uploader_id`. It is the
one search parameter that could otherwise expose unmoderated uploads to anybody
who guessed at it. Tested by
`test_the_status_filter_cannot_be_pointed_at_the_moderation_queue`.

### Testing

**Q: How do you test the database?**
Every test gets a **fresh SQLite file migrated with Alembic**, not
`create_all()`. The CHECK constraints, the UNIQUE constraints and the FTS5
triggers only exist in the migration — a suite built from the model classes would
be testing a schema the application never runs on.

**Q: How do you test the file upload?**
`conftest.py` builds a **genuinely valid PDF** by hand, xref offsets and all, and
posts it through real multipart. A stub starting with `%PDF-` would pass the
magic-byte check and then silently cost every listing its preview, because
preview generation deliberately swallows its own failures.

**Q: How do you know a background task did not write to your real database?**
`_isolate_background_sessions` rebinds **every** module-level `SessionLocal` in
`sys.modules`, not just the one in `app.core.db`. `from x import y` binds by
value at import time, so `materials.py`, `requests.py` and the reminder job each
hold their own copy. Missing one would not fail a test — it would quietly write
demand events into the developer's real `notevault.db` while the assertions
still passed.

---

## 18. Live demo script

Ten minutes, all four features, two browser windows (one normal, one private).

**Setup:** `uvicorn` running, `http://localhost:8000` open. Have `/docs` in a
third tab and `sqlite3 notevault.db` in a terminal.

| # | Do this | Say this | FR |
|---|---|---|---|
| 1 | **Private window** → sign in as `student2` → **Requests** → *Post a request* → course `CSE470`, max price `600` → Post | "A request is a saved search, not just a want-ad. Those criteria are what the matcher will compare new listings against." | 2.1 |
| 2 | Show the toast: *"We will notify you when a CSE470 listing matches it."* | "Status is OPEN. It is now watching." | 2.1 |
| 3 | **Normal window** → sign in as `student1` → **Upload** | | 1.1 |
| 4 | Type title `Software Engineering Notes`, course `CSE470` — **pause** | "Two panels just appeared on their own, and I did not click anything." | |
| 5 | Point at the duplicate panel | "It found similar listings. It **warns**, it does not block — two students selling the same textbook is normal. But I must tick the box, and if the course code changes the tick resets." | 1.5 |
| 6 | Point at the price panel | "৳X suggested, based on N similar listings, and it tells me **which rule** produced that — this exact course, or a widened department sample. If it had fewer than three it would say so instead of guessing." | 3.1 |
| 7 | Attach a PDF, pick SELL, click **Apply** on the suggested price, submit | | 1.1 |
| 8 | Read the toast aloud | *"It is awaiting moderation. Nobody can find it in browse until a moderator approves it."* | 1.1 |
| 9 | Go to **Browse**, search `CSE470` | "It is not there. It is PENDING and invisible — not even by direct link, which returns 404 rather than 403 so we do not confirm it exists." | 4.1 |
| 10 | **Terminal:** `SELECT title, status, file_path, page_count FROM study_materials ORDER BY upload_date DESC LIMIT 1;` | "The row exists, status PENDING, the file is on disk under a generated UUID, and the page count came from actually parsing the PDF." | 1.1 |
| 11 | Sign out → sign in as **`admin@notevault.test`** → **Admin panel** | "The counter is live off the queue itself, so it can never disagree with the queue page." | 4.1 |
| 12 | Open **Moderation queue** | "Oldest first. Newest-first would starve the bottom of the queue, which is the exact failure a moderation panel exists to prevent." | 4.1 |
| 13 | Click **Reject** on nothing — show the modal demanding a note, then cancel | "A rejection with no reason is indistinguishable from the site being broken. The uploader sees this note verbatim." | 4.1 |
| 14 | Click **Approve** | | 4.1 |
| 15 | **Read the toast aloud: "Approved — 1 student notified"** | "**This is the moment.** Approval did not just flip a status. It ran the auto-matcher, which found student2's open request for CSE470, checked the budget, and notified them. That number comes back in the response, which is why the matcher runs inline rather than in the background." | 4.1 → 2.1 |
| 16 | **Private window:** the bell has a badge. Click it. | "Clicking the notification opens the listing, not the request — the useful next click is opening the material." | 2.1 |
| 17 | **Requests** page: the request now reads **MATCHED** | "The board stops advertising a want that has been answered, and that request can never fire again." | 2.1 |
| 18 | **Terminal:** `SELECT type, message, ref_type FROM notifications ORDER BY created_at DESC LIMIT 3;` | "Persisted first, pushed second. If student2 had been offline, the row would still be here on next login." | 2.1 |
| 19 | Back as **admin**, click **Approve** again on the same listing (via `/docs` if the row is gone) | "409. A double-click cannot re-notify everyone." | 4.1 |

**If something breaks mid-demo:** open `/docs`, authorize, and call the endpoint
directly. It is generated from the code, so it always matches what is running.

---

## 19. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` | Wrong working directory or `PYTHONPATH` | `cd backend` before `uvicorn`/`pytest`, or `export PYTHONPATH="$PWD/backend"` from the repo root |
| `conda: command not found` | conda not on PATH | `export PATH="$HOME/miniconda3/bin:$PATH"` |
| Upload returns **415** | Magic bytes are not PDF/PNG/JPEG | Check the file really is what its name says. Renaming does not help — that is the point. |
| Upload returns **413** | Over `MAX_UPLOAD_MB` (25) | Compress the PDF, or raise the value in `.env` |
| Upload returns **422** with "price" | Price on a FREE/EXCHANGE listing, or missing on SELL/RENT | Match the price to the listing type |
| `has_preview: false` after upload | The PDF is encrypted or malformed | Expected behaviour — previews are best-effort by design. Check the server log for the warning. |
| Uploaded listing not in Browse | It is PENDING | Approve it as admin, or set `AUTO_APPROVE_LISTINGS=true` in `.env` and restart |
| Price suggestion always `insufficient_data` | Fewer than 3 APPROVED/RESERVED/COMPLETED priced listings for that course in the last 180 days | `python scripts/seed.py --reset`, or approve more listings, or lower `PRICE_MIN_SAMPLE` |
| Admin panel returns **403** | Signed in as a student | Sign in as `admin@notevault.test` |
| Admin panel returns **401** | No token / expired and refresh failed | Sign in again |
| Approve returns **409** | Already approved (or rejected) | Reload the queue — this is the double-click guard working |
| `notified: 0` on approve | No OPEN request matches, or the only match is the uploader's own | Post a request from a *different* account first, with criteria that do not contradict the listing |
| Request stays OPEN after a matching approval | One criterion does not match (department, type, campus, budget, edition) | Compare the request's criteria to the listing's fields. Remember NULL means "any". |
| Frontend shows stale data | Query cache not invalidated | Hard reload. In dev, check `invalidateQueries` is called in the mutation's `onSuccess`. |
| `database is locked` | Two writers at once | The app sets `busy_timeout=5000`. Close any `sqlite3` shell holding a write transaction. |
| Tests write to the real database | A module's `SessionLocal` was not rebound | Confirm `_isolate_background_sessions` covers it — it sweeps every `app.*` module, but a new module must be imported before the sweep runs |
| Camera does not work for QR | Not a secure context | Demo on `http://localhost`, not a LAN IP. (Not one of these four features, but it comes up.) |
| Rental reminders fire more than once | `uvicorn --workers > 1` | Always `--workers 1` |

### Reading the server logs

Uvicorn prints every request. The lines that matter for these features:

```
INFO  "POST /api/v1/materials HTTP/1.1" 201 Created           ← FR 1.1 worked
WARNING preview generation failed for abc123.pdf              ← FR 1.1 degraded
INFO  "POST /api/v1/requests HTTP/1.1" 201 Created            ← FR 2.1
WARNING immediate match scan failed for request xyz           ← FR 2.1 background
INFO  "GET /api/v1/materials/price-suggestion… " 200 OK       ← FR 3.1
WARNING duplicate check failed for 'CSE470'                   ← FR 1.5 degraded
INFO  "POST /api/v1/admin/materials/abc/approve" 200 OK       ← FR 4.1
WARNING auto-match failed for listing abc123                  ← FR 2.3 background
```

The `WARNING` lines are all from code that **deliberately swallows its own
failures** — preview generation, the duplicate check, the demand log, the
background matcher. None of them will ever fail a user's request. If you see one,
the feature degraded gracefully; it did not break.

---

## Appendix — the one-page cheat sheet

```
FEATURE          ENDPOINT                              SERVICE            TABLE
────────────────────────────────────────────────────────────────────────────────
FR 1.1 Upload    POST /materials                       storage.py         study_materials
FR 2.1 Requests  POST/GET/PATCH/DELETE /requests       matcher, notifier  requests
FR 3.1 Price     GET  /materials/price-suggestion      recommendation.py  (reads materials)
FR 4.1 Admin     GET  /admin/moderation/queue          moderation.py      study_materials
                 POST /admin/materials/{id}/approve       + matcher.py    + notifications

STATUSES
  Listing:  PENDING → APPROVED → RESERVED → COMPLETED
                ↘ REJECTED        ↘ REMOVED (from any status)
  Request:  OPEN → MATCHED (matcher only)   OPEN → CLOSED (owner only)
  Report:   OPEN → RESOLVED | DISMISSED

STATUS CODES YOU WILL BE ASKED ABOUT
  401  no token                        403  token, wrong role/owner
  404  hidden listing (never 403!)     409  double-approve, edit a live listing,
  413  file too big                         set MATCHED by hand
  415  wrong file type (magic bytes)   422  bad metadata, missing reject note

THE ONE SENTENCE
  Upload lands PENDING → admin approves → the matcher notifies everyone whose
  request board entry asked for that course. Price suggestion advises the seller
  on the way in.

THE ONE COLUMN
  course_code_norm — 'cse 470', 'CSE-470' and 'Cse470' all become 'CSE470'.
  It is why duplicate detection, price suggestion, auto-match and trending all
  agree on what "the same course" means.

THE ONE FILE TO OPEN FIRST
  backend/app/api/v1/materials.py:477   create_material()   ← FR 1.1
  backend/app/api/v1/admin.py:218       approve_material()  ← FR 4.1
```
