# Trainee access implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow accountless trainees to read explicitly shared cards using a revocable common-code session.
**Architecture:** Separate trainee JWT/API/read-only frontend routes. Database singleton code/version shared by workers. Existing member authorization remains unchanged except explicit rejection of trainee tokens.
**Tech Stack:** FastAPI, SQLAlchemy, SQLite, python-jose, React 19, TypeScript, React Query.
**Spec:** ../specs/2026-11-03-trainee-access-design.md

## Global Constraints
- 30-day trainee token; every read validates current code version. No account/logout buttons for trainees.
- Only admin/chair/deputies may view/rotate code, enforced by server.
- Existing cards remain private to members until explicitly shared; preserve app.db.
- New generated raccoon asset with CSS animation and reduced-motion support.
- Work in feat/trainee-access branches. No push/deploy.

## Review Focus
- Trainee JWT never grants member privileges, including if a user named trainee exists.
- Simultaneous code rotations must not revive an older session or permit two current codes.
- Guest reads, inline Markdown users blocks and navigation must not expose member-only data.
- Session reload, code rotation, expiry and switching to member login must clear cached content correctly.
- Old SQLite schemas upgrade safely without data loss; no tests touch the local app.db.

### Task 1: Backend authorization and guest reading
Files: models.py, database.py, schemas.py, main.py; create trainee_access.py and tests/test_trainee_access.py.
Interfaces: GET/POST /api/trainee-code -> {code: string|null, updated_at: string|null}; POST /api/trainee/login {code} -> Token; GET /api/trainee/session -> {expires_at}; GET /api/trainee/categories and /cards and /cards/{id} -> public read schemas.
- [x] Write integration tests using isolated temporary SQLite and HTTPX ASGITransport: role matrix; private-by-default migration; login/expiry/rotation; read filtering; no member data; persistence; ordinary login unchanged.
- [x] Run tests and observe failure due to missing endpoints/fields.
- [x] Implement code singleton, random code/version, explicit trainee token type, 30-day expiry, independent guest dependencies, read schemas and permission flag CRUD/audit.
- [x] Run unittest suite to green; verify actual app.db unchanged.

### Task 2: Frontend guest flow and administration
Files: create api/trainee.ts, stores/trainee.ts, components/TraineeRoute.tsx, pages/trainee/{login,home,card,manage}.tsx, components/TraineeRaccoon.tsx, styles/trainee.css; modify routes.tsx, Header.tsx, login.tsx, card/page.tsx, add-card.tsx, api/category.tsx, types/Category.tsx and MarkdownRenderer.tsx.
Interfaces: separate trainee token storage and query keys; guest routes /trainee/login, /trainee, /trainee/card/:cardId; member-only /trainee-access management. Member login explicitly clears guest session; guest API 401 clears trainee token and redirects to guest login with message.
- [x] Verify guest session lifecycle and management permissions with meaningful automated tests where tooling allows; use TypeScript and browser smoke verification for UI.
- [x] Build guest-only routes with header containing only knowledge-base navigation and trainee label; no member API queries.
- [x] Add management page and header link, role guards; code copy/rotation, clear error/loading/empty states.
- [x] Add private-default trainee visibility checkbox to card create/update, persisted through API.
- [x] Add generated raccoon PNG, animated button and guest entry form; reduced-motion and mobile layout.
- [x] Add Markdown option disabling dynamic user blocks for guest render, preserving nested block behavior.
- [x] Run tsc, ESLint, Vite build, frontend behavioral tests and available browser smoke checks.

### Task 3: Review and documentation
- [x] Independent code/security review of both repositories; fix important findings with regression tests.
- [x] Re-run relevant checks after fixes. Update AGENT.md with endpoints, schema upgrade, guest lifecycle and command evidence.
- [x] Report implemented behavior and any verified tooling limitations. Leave all changes reviewable, do not push/deploy.
