# Mezbah — Assigned Feature Files

This bundle preserves the original NoteVault paths for Mezbah's assigned work:

- Module 1, Feature 4: File Preview
- Module 2, Feature 2: In-app Messaging
- Module 3, Feature 3: QR Handoff Verification
- Module 4, Feature 4: Trending Courses

## Important

This is a reference/transfer bundle, not a standalone runnable application.
Several included files are shared with features owned by other members. Git
features are not cleanly separable by file in this project because, for example,
`materials.py`, `models/tables.py`, `lib/types.ts`, and `Layout.tsx` implement
multiple requirements.

For team development, create each member's branch from the same complete base
repository and commit only that member's changes. Do not delete files owned by
other members merely to make a branch appear smaller.

## Module 1 — File Preview

- `frontend/src/features/listings/ListingDetailPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `backend/app/api/v1/materials.py`
- `backend/app/services/storage.py`
- `backend/app/schemas/material.py`
- `backend/app/models/tables.py`
- `backend/app/core/config.py`
- `backend/tests/test_materials.py`

## Module 2 — In-app Messaging

- `frontend/src/features/community/MessagesPage.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/src/lib/ws.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `backend/app/api/v1/chat.py`
- `backend/app/ws/chat.py`
- `backend/app/ws/manager.py`
- `backend/app/schemas/community.py`
- `backend/app/models/tables.py`
- `backend/app/services/notifier.py`
- `backend/app/main.py`

## Module 3 — QR Handoff Verification

- `frontend/src/features/transactions/TransactionDetailPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `backend/app/api/v1/transactions.py`
- `backend/app/services/qr.py`
- `backend/app/services/transactions.py`
- `backend/app/core/security.py`
- `backend/app/core/config.py`
- `backend/app/schemas/transaction.py`
- `backend/app/models/tables.py`
- `backend/app/models/enums.py`
- `backend/tests/test_qr.py`
- `backend/tests/test_transactions.py`

## Module 4 — Trending Courses

- `frontend/src/features/analytics/TrendingPage.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `backend/app/api/v1/analytics.py`
- `backend/app/services/analytics.py`
- `backend/app/services/demand.py`
- `backend/app/api/v1/materials.py`
- `backend/app/api/v1/requests.py`
- `backend/app/api/v1/wishlist.py`
- `backend/app/schemas/analytics.py`
- `backend/app/models/tables.py`
- `backend/app/models/enums.py`
- `backend/scripts/seed.py`

## Shared wiring and dependencies

- `frontend/src/App.tsx`
- `frontend/package.json`
- `frontend/package-lock.json`
- `backend/app/api/v1/__init__.py`
- `backend/app/models/__init__.py`
- `backend/app/schemas/__init__.py`
- `backend/alembic/versions/0001_initial_schema.py`
- `environment.yml`
- `README.md`
