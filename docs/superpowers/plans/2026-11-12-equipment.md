# Equipment Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add inventory, self checkout, manager editing and public member history modal.
**Architecture:** Additive SQLAlchemy tables and equipment.py router; React Query page.
**Tech Stack:** Existing FastAPI/Pydantic/SQLAlchemy, React/TypeScript, no new dependencies.
**Spec:** ../specs/2026-11-12-equipment-design.md

## Global Constraints
Preserve existing app.db; tests use existing temporary database harness.
Only active members, no accountless trainee access. No deployment or push requested here.
Work on feat/equipment in existing checkouts, preserving pre-existing database changes.

## Review Focus
Authorization bypass via direct API; forged checkout owner/date; stale simultaneous actions;
history consistency; inactive users and duplicate storage names.

## Tasks
- [x] Add failing real API tests in tests/test_equipment.py; add frontend behavior/API tests.
- [x] Add EquipmentLocation, EquipmentItem and EquipmentHistory to models.py.
- [x] Add equipment.py with authenticated options/list/create/update/take/history endpoints.
- [x] Wire router in main.py; verify whole backend suite including existing trainee tests.
- [x] Add ../legacy-frontend/src/api/equipment.ts and equipment page/components/CSS.
- [x] Add protected route and navigation. Server-provided can_manage governs manual controls.
- [x] Run frontend tests, build/lint, browser smoke where tools permit, and independent review.
- [x] Document endpoints/behavior; verify original database checksum and final diffs.

## Ledger
Baseline: backend 16 tests pass, frontend 9 tests pass. Existing deprecation warnings only.
User explicitly added immutable per-item history modal visible to all members.

Final verification: 29 backend tests and 14 frontend tests passed; TypeScript/Vite build
and ESLint passed. Existing Node-version/chunk-size and Python deprecation warnings remain.
Chrome end-to-end passed with clean temporary DB: create/default storage/new storage,
member take/profile links/history/Escape/search/manual date, full leadership navigation,
375px layout and centered history dialog, no JS runtime errors.
Independent review found an overbroad generic department-deputy predicate; dedicated equipment
predicate and a red/green negative role regression fixed it. Re-review found no important issues.
Original app.db SHA256 unchanged: 929e294795bffedd04980edb79b9859df998282da358f0f46292acdd825a2d7c.
Both repos remain on feat/equipment for review; no push or production deployment performed.
