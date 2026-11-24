# Budget, saved payout details and personal data implementation plan

Goal: implement the four changes approved in chat on 2026-11-24.
Architecture: shared backend records for web and confirmed Telegram identities; snapshot payout text in submitted requests. Preserve ledger and custody accounting, combine only the displayed ledger total.
Tech stack: existing FastAPI/SQLAlchemy/SQLite, React/TypeScript, Python Telegram client.
Spec: approved four-point design in this conversation.

Global constraints: maximum three private saved payout records per user; no automatic saving from requests; no production DB resets; configured operator identity, address, contact, hosting/processors and retention required for valid consent. Keep existing app.db untouched.

Review focus: concurrent creation at the three-record limit; cross-user saved record selection/deletion; policy changes invalidate consent; revoked consent clears optional saved data; bot retries do not duplicate records or submissions.

### Task 1: backend personal data and saved payout records
Files: models.py, personal_data.py, main.py, budget.py, tests/test_personal_data.py.
Interfaces: public policy; authenticated consent status/accept/revoke and saved-detail list/create/delete; mirrored routes under /api/telegram/bot. RequestCreate.saved_payout_id resolves to immutable payout_details snapshot.
- [x] Add failing API tests: missing configuration, unconsented storage, max three, ownership, idempotency, snapshot, revocation, concurrent adds.
- [x] Run isolated unittest tests; confirm missing routes fail.
- [x] Implement records, policy configuration and transactional routes; add request selection validation.
- [x] Run new tests and existing backend suite against temporary DB.

### Task 2: site
Files: api/personal-data.ts, components/PersonalData.tsx, components/SavedPayoutDetails.tsx, components/ProtectedRoute.tsx, pages/user/about.tsx, pages/budget/page.tsx, pages/budget/treasurers.tsx, api/budget.ts, tests/budget.test.tsx.
Interfaces: Task 1 routes; display consent gate before protected children and optional storage consent in own profile; one total; custody mismatch warning in integer kopecks.
- [x] Add failing rendering tests for combined total and mismatched/matched holdings.
- [x] Implement website UI using existing React Query and API patterns.
- [x] Run frontend tests, build and lint.

### Task 3: bot
Files: budget_form.py, tests/test_budget.py, tests/test_saved_payouts.py.
Interfaces: Task 1 bot routes, existing durable state and callback ownership.
- [x] Add failing tests for bank hint, saved selection and management, cancellation, stale selection and consent flow.
- [x] Implement private budget saved-details menu and explicit save consent; fresh request details remain unsaved.
- [x] Run full bot suite.

### Task 4: review and finish
- [x] Document required personal-data settings and operational limitations.
- [x] Fresh code review, fix important issues with regression tests; rerun relevant checks.
- [x] Report exact verification and remaining operator configuration; do not deploy without an explicit deployment request.

Decisions: work in the existing three checkouts to retain shared dependencies and cross-repository smoke fixtures; only pre-existing change is legacy-back/app.db. Native execution, following user instruction to proceed without more design questions.


Execution record: initial new API and SSR tests failed with missing routes/display, then passed after implementation. Review by independent agent found ID reuse, missing submit consent validation, unguarded registration screens, stale optional consent display, version checkbox binding, and stale template draft loss. Added regressions and fixed all findings. SQLite template IDs use AUTOINCREMENT; request consent is checked inside the actor write transaction. Old manual budget fixtures explicitly seed service consent; personal-data tests opt out. Whole backend suite: 128 tests pass. Frontend: 38 tests, production build and lint pass. Bot: full suite passes. Isolated Chrome scenario passes on desktop/mobile including consent, saved templates, max three, delete, combined total, mismatch warning, login roles and CRUD budget regressions. Pre-existing legacy-back/app.db was never used by these tests or intentionally changed.

Ruling: unavailable real operator/legal settings remain required environment configuration, not invented identity; production release must fill all five settings first or member entry/new budget requests are blocked. Deletion of historical requests/account data is an operator procedure; only optional payout templates are automatically deleted on withdrawal. This limitation is documented in PERSONAL_DATA.md. Changes remain local; no deployment was requested.


User override before push (2026-11-24): omit operator name/address; link admin account for contact; primary website storage in Belarus, authenticated member access by role; no fixed calendar retention, preserving withdrawal/deletion and purpose limits. Replace required-settings activation gate with working defaults. This supersedes the earlier required-operator-configuration ruling; legal incompleteness is explicitly documented. Push all three verified repositories as requested, excluding pre-existing local app.db.
