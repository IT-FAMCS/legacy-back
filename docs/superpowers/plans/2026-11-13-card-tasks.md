# Card Tasks Implementation Plan
> Use superpowers:executing-plans; implement inline and obtain whole-branch review.
**Goal:** Shared audited tasks in cards and a dedicated Tasks page.
**Architecture:** SQLAlchemy task, department-link, assignee and history tables; FastAPI router factory consumes existing auth/card checks. React Query shares data between task views.
**Tech Stack:** Existing Python/FastAPI/SQLite and React/TypeScript/Vite; no new runtime dependencies.
**Spec:** ../specs/2026-11-13-card-tasks-design.md (approved in conversation).
## Global Constraints
Preserve existing DBs; use temporary test DBs. Only chair/deputies delete tasks. All accessible users edit tasks. No Telegram delivery. Existing cards supply departments/events.
## Review Focus
Restricted linked cards/history must not leak. Concurrent edits return conflict. Archive preserves history. Inactive assignees remain on existing tasks. Empty/global groups and mobile forms remain usable.
## Task 1: Backend task domain
Files: models.py, tasks.py, main.py, tests/test_tasks.py.
Interface: GET /api/tasks/options; GET/POST /api/tasks; GET/PATCH/DELETE /api/tasks/{id}; GET /api/tasks/{id}/history. Task payload title, description, deadline, event_card_id, department_card_ids, assignee_ids, completed; PATCH/DELETE require version. Lists accept card_id and archived. Options returns visible eligible cards, active assignees, can_delete.
- [x] Write API tests for shared identity, general tasks, all-user edits, role-specific archive, history, invalid links/access, old assignments, conflicts and card lifecycle.
- [x] Run tests, verify missing routes fail.
- [x] Implement relational models, validated schemas, router, atomic audit/version handling and card-delete/category-move protection.
- [x] Run new tests then full backend suite.
## Task 2: Frontend task views
Files: src/api/tasks.ts, src/features/tasks/{TasksBoard,TaskEditor,TaskHistory}.tsx, tasks.css, task-view.ts; src/routes.tsx, components/Header.tsx, pages/card/page.tsx; tests/tasks.test.tsx.
Interface: shared types match backend; board accepts optional card context. All mutations invalidate task lists/details/history, and options after updates.
- [x] Write grouping/filtering/overdue tests, verify failure before implementation.
- [x] Implement API, pure grouping helpers, accessible editor/history and table board; wire route/nav/card tabs.
- [x] Run npm test, npm run lint, npm run build.
## Task 3: Integration and review
Files: tests/serve_tasks_smoke.py, tests/browser-tasks-smoke.mjs, documentation and progress.md.
- [x] Exercise real browser: create/edit/complete/reopen shared task, history, general task and mobile viewport; capture screenshots.
- [x] Obtain fresh whole-branch review, fix material findings and verify affected behavior.
- [x] Record checks and limitations; commit feature in both branches, leave reviewable without deploying.
