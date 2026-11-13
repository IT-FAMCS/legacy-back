# Execution ledger: docs/superpowers/plans/2026-11-13-card-tasks.md
Pre-flight: API and frontend use integer card/account IDs, ISO date and UTC timestamps, integer version and can_delete capability.
Ruling: user authorized inline execution, preserve original database in original worktree, use isolated worktrees and temporary test DBs.
Task 1: complete. Task API tests observed RED (404) before implementation then GREEN. Full suite 51 tests passed.
Task 2: complete. Grouping/filter/deadline test observed RED then GREEN; conflict rebase test observed RED then GREEN. Frontend 21 tests, lint and build passed.
Task 3: complete. Whole-branch review found lifecycle race and unrecoverable stale editor. Both fixed and re-reviewed without important findings. Concurrent lifecycle test observed RED before transaction lock and GREEN after. Browser covers two-user conflict, shared views, history and roles; PASS including 390px mobile viewport and editor.
Ruling: archive preserves records and has no restore command in this release. Linking/deleting/recategorizing cards serializes with SQLite BEGIN IMMEDIATE across workers. Hidden screen-reader table labels must be positioned inside their scrollport to avoid enlarging the mobile viewport.
Known environment notices: local Node 20.15.1 is below Vite recommendation 20.19+, but build succeeds; existing bundle size notice remains. No dependency changes.
Integration: keep local feat/card-tasks branches and worktrees for review; do not deploy or merge as part of implementation.
