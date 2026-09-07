# Zamiul — Assigned Feature Files

This bundle preserves the original NoteVault paths for Zamiul's assigned work:

- Module 1, Feature 1: Upload Study Material (FR 1.1)
- Module 2, Feature 1: Request Board (FR 2.1)
- Module 3, Feature 1: Price Suggestion Engine (FR 3.1)
- Module 4, Feature 1: Admin Moderation Panel (FR 4.1)

## Important

This is a reference/transfer bundle, not a standalone runnable application.
Several included files are shared with features owned by other members. Git
features are not cleanly separable by file in this project because, for example,
`materials.py`, `models/tables.py`, `lib/types.ts` and `App.tsx` implement
multiple requirements.

For team development, create each member's branch from the same complete base
repository and commit only that member's changes. Do not delete files owned by
other members merely to make a branch appear smaller.

See `README.md` in this folder for the full walkthrough of how these four
features work end to end.

## Module 1 — Upload Study Material (FR 1.1)

- `frontend/src/features/listings/UploadPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `backend/app/api/v1/materials.py`
- `backend/app/services/storage.py`
- `backend/app/services/matcher.py`
- `backend/app/schemas/material.py`
- `backend/app/models/tables.py`
- `backend/app/models/enums.py`
- `backend/app/core/config.py`
- `backend/app/core/util.py`
- `backend/tests/test_materials.py`

## Module 2 — Request Board (FR 2.1)

- `frontend/src/features/community/RequestsPage.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `backend/app/api/v1/requests.py`
- `backend/app/services/matcher.py`
- `backend/app/services/notifier.py`
- `backend/app/services/demand.py`
- `backend/app/schemas/community.py`
- `backend/app/models/tables.py`
- `backend/app/models/enums.py`
- `backend/app/core/util.py`
- `backend/tests/test_matcher.py`

## Module 3 — Price Suggestion Engine (FR 3.1)

- `frontend/src/features/listings/UploadPage.tsx`
- `frontend/src/lib/types.ts`
- `backend/app/api/v1/materials.py`
- `backend/app/services/recommendation.py`
- `backend/app/schemas/material.py`
- `backend/app/models/enums.py`
- `backend/app/core/config.py`
- `backend/app/core/util.py`
- `backend/tests/test_recommendation.py`

## Module 4 — Admin Moderation Panel (FR 4.1)

- `frontend/src/features/admin/AdminHomePage.tsx`
- `frontend/src/features/admin/ModerationPage.tsx`
- `frontend/src/App.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `backend/app/api/v1/admin.py`
- `backend/app/services/moderation.py`
- `backend/app/services/matcher.py`
- `backend/app/services/notifier.py`
- `backend/app/schemas/admin.py`
- `backend/app/core/deps.py`
- `backend/app/models/tables.py`
- `backend/app/models/enums.py`
- `backend/tests/test_authz.py`

## Shared wiring and dependencies

- `frontend/src/App.tsx`
- `frontend/package.json`
- `frontend/package-lock.json`
- `backend/app/main.py`
- `backend/app/api/v1/__init__.py`
- `backend/app/models/__init__.py`
- `backend/app/schemas/__init__.py`
- `backend/app/schemas/common.py`
- `backend/app/core/config.py`
- `backend/app/core/db.py`
- `backend/app/core/deps.py`
- `backend/app/core/security.py`
- `backend/app/core/util.py`
- `backend/alembic/versions/0001_initial_schema.py`
- `backend/scripts/seed.py`
- `backend/tests/conftest.py`
- `environment.yml`
- `README.md`
