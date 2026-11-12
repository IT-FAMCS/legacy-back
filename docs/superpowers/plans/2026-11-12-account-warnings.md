# Account Warnings Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; independent UI tasks delegated under dispatching-parallel-agents.

**Goal:** Extend accounts, protect warning records and add equipment removal with polished dialogs.
**Architecture:** Nullable user columns/additive migration, Warning model and focused router, protected
warning permissions/counts/history endpoints. Existing equipment gets soft deletion. React Query panels.
**Tech Stack:** Existing SQLAlchemy/FastAPI/Pydantic and React/TypeScript; no dependencies.
**Spec:** ../specs/2026-11-12-account-warnings-design.md

## Constraints
Never modify source app.db; no unrequested production push/deploy. Standing user authorization
covers routine development. System sandbox approvals remain mandatory.

## Review Focus
Warning leaks through direct API; student-chair view/manage distinction; mandatory reasons;
double removal/concurrent deletion; nullable fields round trip and pre-existing DB migration.

## Tasks
- [x] Add failing backend tests for contact fields, warnings permissions/count/history/removal, migration and equipment archive.
- [x] Implement models/schema/migrations, warning routes and permissions, archive endpoint.
- [x] Implement account field forms/CSV/list (delegated, file ownership explicit).
- [x] Implement modal/hover polish (delegated, file ownership explicit).
- [x] Integrate warning panel/counts, account profile equipment, deletion controls/confirmation.
- [x] Run whole test suites, TypeScript/build/lint, browser flows and independent review.
- [x] Update documentation and verify source DB unchanged.

## Verification
40 backend tests, 19 frontend TAP tests plus focused button/contact assertions passed.
TypeScript/Vite build and ESLint passed. Existing Python deprecation/Node-version/chunk warnings remain.
Independent review: no actionable correctness, security, migration or data-loss findings.
Chrome end-to-end passed contact profile/list/edit, required reasons, issue/remove audit, counts,
ordinary-member privacy, department/student-chair view-only access, nested dialogs/Escape/phone,
held equipment/profile/archive/history and stable hover bounds with no JS runtime errors.
Source app.db SHA256 unchanged: 929e294795bffedd04980edb79b9859df998282da358f0f46292acdd825a2d7c.
