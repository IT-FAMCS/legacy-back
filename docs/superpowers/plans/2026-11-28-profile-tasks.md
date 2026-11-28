# Profile and task improvements Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the ten approved profile, department, task, report and mascot changes.
**Architecture:** Extend existing APIs, preserve permissions and versioned history. Standalone report module generates OOXML DOCX. Account setting controls mascot.
**Tech Stack:** FastAPI, SQLAlchemy, React, TypeScript, CSS, Python zipfile/XML.
**Spec:** ../specs/2026-11-28-profile-tasks-design.md

## Global Constraints
Preserve local app.db; no push; retain existing privileged archive/department and warning access rules.

## Tasks
- [x] 1. Profile self-edit allowlist and raccoon preference, schema upgrade; test participant denial, privileged edit, protected fields, preference independence.
- [x] 2. Versioned atomic bulk archive and restore with audit; test permissions, stale versions, incomplete/hidden tasks, restored status.
- [x] 3. DOCX endpoint and generator; test XML, contents, completion date, warnings and visibility.
- [x] 4. UI filters, users without departments, department deletion, bulk archive/restore, report form and download; test deadline boundaries and API payloads.
- [x] 5. Generated mascot, ten phrases, account toggle, minimum loader duration; reduced-motion support.
- [x] 6. Backend/frontend suites, build, lint, independent review and corrections.

## Review Focus
Self-edit escalation; concurrent bulk actions; restricted-card/report data leaks; incorrect department mapping; mascot timers/reduced motion and failure states.

## Implementation record
- Implemented on local `feat/profile-tasks-raccoon` branches; original app.db excluded.
- Independent reviewer found optional-field clearing, stale session response, report scope clarity, concurrent department deletion and bulk query issues. Fixed; regression coverage added.
- Source card and selected department roster are explicitly identified in report UI and DOCX because these entities have no existing stored mapping.
- Bulk archive validates all selected task versions before changes, batches graph and notification binding reads, preserves audit and notification behavior. Up to 5000 selected tasks per request, with an explicit UI error above that limit.
- Frontend: 42 tests, build and lint passed. Browser scenario passed for self edit/clearing, mascot preference persistence, deadline/unassigned/no-departments filters, bulk archive/restore, DOCX download and mobile placement.
- Final small changes: minimum task loader, disabled duplicate action buttons. Production build passed afterward. Additional browser assertions for department removal/minimum loading were prepared but not run after user requested minimal testing.
- Final backend regression result is recorded in the completion commit/session output. No additional test runs after the user's request.
