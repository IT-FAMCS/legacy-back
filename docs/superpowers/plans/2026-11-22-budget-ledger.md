# Budget Ledger and Reimbursement Requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a shared BYN budget ledger for the website and Telegram bot, including audited reimbursement and add-funds requests.

**Architecture:** Keep `legacy-back` as the single financial API and database. The website uses authenticated budget endpoints; `tg-bot` uses the backend’s existing linked-user and service-authentication patterns. Cash and card balances are derived from approved/manual ledger entries, and durable bot notifications carry request, correction, decision, and keyboard-removal events.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite schema upgrades, Pydantic, React 19, TypeScript, Vite, Telegram Bot API, existing SQLite-backed bot worker queue.

**Spec:** `docs/superpowers/specs/2026-11-22-budget-design.md`

## Global Constraints

- Start both balances at zero; do not import historical data.
- Use BYN with two decimal places for entry and display.
- Backend authorization is authoritative; hiding the Budget tab is only a user-interface convenience.
- Request decisions and their ledger effects are committed atomically and cannot be processed twice.
- Receipt and supporting photos are kept out of website responses; Telegram file identifiers are used only by the bot to send them to cashiers.
- Keep the website and bot on the same `legacy-back` API.
- Do not rewrite the existing local `app.db` contents.

## Review Focus

- **Unauthorized account or stale role:** budget APIs return 403, and the navigation/page do not expose finance data.
- **Invalid amount:** zero, negative, non-finite, or more than two decimal places is rejected; amounts are positive and bounded by the database field.
- **Two cashiers decide concurrently:** exactly one pending transition and one linked ledger entry are committed.
- **Amount is edited while notifications are pending:** every still-actionable cashier message shows the corrected amount before approval can create an entry.
- **No linked, active kaznachey exists:** reject request confirmation without losing the participant’s draft or receipt, and explain that the finance team must link/enable a cashier.

---

## File map

### `legacy-back`

- Modify `models.py`: add the position flag and budget ledger, request, amount-history, notification-outbox, and delivery records.
- Modify `schemas.py`: expose the position flag and define request/entry API payloads.
- Modify `database.py`: add a backward-compatible SQLite upgrade for `positions.is_kaznachey`; rely on metadata creation for new tables.
- Modify `main.py`: include the budget router and expose the caller’s finance capability in the current-user response.
- Create `budget.py`: own finance authorization, ledger and request endpoints, atomic decisions, and durable notification claiming/acknowledgement.

### `legacy-frontend`

- Modify `src/types/Position.tsx`, `src/api/user.tsx`, `src/stores/user.tsx`, and `src/pages/positions/list.tsx` to edit and expose `is_kaznachey`.
- Modify `src/components/Header.tsx` and `src/routes.tsx` to expose a protected `/budget` page to the finance group.
- Create `src/api/budget.ts` for budget and request calls.
- Create `src/pages/budget/page.tsx` and `src/pages/budget/budget.css` for the balances, yearly groups with separate monthly tables, editable transactions, and pending-request tab.

### `tg-bot`

- Create `budget_form.py` as a `Chat` mixin for request wizard and cashier callbacks; modify `chat.py` to route the Budget menu, photos, and mixin actions.
- Modify `worker.py` to deliver budget outbox events, send receipt photos, update corrected request messages, remove processed buttons, and acknowledge delivered message IDs.
- Reuse `store.py` draft and callback storage; extend only if delivery coordination needs a durable local field beyond the backend delivery records.

## Task 1: Position Permission and Finance Data Model

**Files:**
- Modify: `legacy-back/models.py`
- Modify: `legacy-back/schemas.py`
- Modify: `legacy-back/database.py`
- Modify: `legacy-back/main.py`
- Modify: `legacy-frontend/src/types/Position.tsx`
- Modify: `legacy-frontend/src/api/user.tsx`
- Modify: `legacy-frontend/src/stores/user.tsx`
- Modify: `legacy-frontend/src/pages/positions/list.tsx`

**Interfaces:**
- `Position.is_kaznachey: bool`, default `False`, is returned by create/list/update position endpoints.
- `GET /api/user/me` includes `is_kaznachey` and `can_view_budget` so navigation uses backend-derived capability.
- `BudgetRequest` records `request_type` (`reimbursement` or `add_funds`), applicant, department/name snapshots, submitted/current amount, optional payout details and Telegram file ID, status, processor, and timestamps.
- `BudgetRequestAmountChange` records request ID, old/new amount, editor ID, and timestamp.
- `BudgetEntry` records `direction` (`income`/`expense`), `account` (`cash`/`card`), positive `Numeric(12,2)` amount, description, operation timestamp, actor/editor IDs, and optional unique request ID.
- `BudgetNotification` and `BudgetNotificationDelivery` provide retryable event delivery and Telegram message IDs needed to update or remove keyboards.

- [x] Add `is_kaznachey = Column(Boolean, nullable=False, default=False, server_default='0')` to `Position` and the corresponding optional create/update and required response fields.
- [x] Add SQLite migration logic in `ensure_schema_upgrades()` that adds `positions.is_kaznachey BOOLEAN NOT NULL DEFAULT 0` only when absent, following existing duplicate-column and multi-worker race handling.
- [x] Add the five finance model classes and SQLAlchemy relationships/constraints described above; use unique `BudgetEntry.request_id` for one ledger effect per approved request.
- [x] Add `is_kaznachey` to the existing `PositionFormData`, create/edit initialization, checkbox list, and `Position`/`PositionCreate` TypeScript types.
- [x] Add `is_kaznachey` and `can_view_budget` to `UserResponse`, `format_user_response()`, and the frontend user-store type; calculate executive access using the existing `is_admin_chair_or_deputy()` helper.
- [x] Review existing `app.db` status and retain its pre-existing local modification; do not seed budget entries or alter current balances.

## Task 2: Ledger, Request API, and Atomic Decisions

**Files:**
- Create: `legacy-back/budget.py`
- Modify: `legacy-back/main.py`
- Modify: `legacy-back/schemas.py`

**Interfaces:**
- `GET /api/budget` returns `{balances: {cash, card}, years: [{year, months: [{month, entries: [...]}]}]}`; entries contain date, direction, account, amount, description, and edit metadata.
- `GET /api/budget/requests?status=pending` returns pending request details and amount history but never image IDs.
- `PATCH /api/budget/requests/{id}` accepts `{amount}` only while pending; it appends an amount-history record and queues updates to all actionable cashier messages.
- `POST /api/budget/entries` accepts `{direction, account, amount, description, operation_at}`; `PATCH /api/budget/entries/{id}` accepts the editable subset. Both require finance authorization.
- `POST /api/telegram/bot/budget/requests` creates a request for the linked caller. Reimbursement requires payout details and a receipt file ID; add-funds accepts optional supporting photo and no payout destination.
- `POST /api/telegram/bot/budget/requests/{id}/decision` accepts `{decision: 'approved'|'rejected', account?: 'cash'|'card'}`. Approval requires account; rejection forbids it.
- `POST /api/telegram/budget/notifications/claim` and `POST /api/telegram/budget/notifications/{id}/ack` use service authentication, lease tokens, delivery outcomes, and returned Telegram message IDs.

- [x] Implement `can_manage_budget(position) -> bool` as `is_admin_chair_or_deputy(position) or position.is_kaznachey`; use it for every website read/write endpoint.
- [x] Implement request/entry Pydantic models with strict types, positive finite amounts quantized to two decimals, bounded strings, and extra-field rejection.
- [x] Implement the monthly/yearly ledger response and calculate cash/card balances from all income and expense entries; return negative balances visibly.
- [x] Implement finance-only manual entry create/edit endpoints and pending-only amount correction. Record editor and history, then queue correction events for linked, active cashiers.
- [x] Implement bot request creation with linked-user identity, department/name snapshots, required reimbursement receipt, optional add-funds evidence, and no request visibility across participants.
- [x] Before request persistence, ensure at least one active linked kaznachey can be notified; otherwise return a clear conflict and leave draft data with the bot caller.
- [x] Implement decision handling with the existing SQLite `BEGIN IMMEDIATE` write serialization pattern. Recheck pending status and current cashier permission inside the write transaction; create exactly one income/expense row using the final amount and selected account; reject stale/concurrent actions without duplicate effects.
- [x] Enqueue request, correction, outcome, and remove-keyboard events in the same database transaction as each corresponding state change. Never expose Telegram file IDs in website responses.
- [x] Register the router in `main.py` with the current-user, bot-actor, and service-authentication dependencies.

## Task 3: Telegram Request Wizard and Cashier Workflow

**Files:**
- Create: `tg-bot/budget_form.py`
- Modify: `tg-bot/chat.py`
- Modify: `tg-bot/worker.py`
- Reuse: `tg-bot/store.py`

**Interfaces:**
- `Chat` includes `BudgetFormMixin`; `budget_menu()`, `budget_action(data, token)`, and `budget_message(message)` own menu actions, callback actions, and draft message handling.
- Chat API paths are relative to `/api/telegram/`: `bot/budget/requests`, `bot/budget/requests/{id}/decision`, and `budget/notifications/claim` / `budget/notifications/{id}/ack`.
- Draft states are namespaced `budget_*`, bound to Telegram generation, and cleared by `/cancel`, unlink, submit, or expiry.
- Bot notification delivery records detail-message and receipt-photo message IDs through the backend ack endpoint.

- [x] Add the Budget button and request type menu to `Chat.main_menu()` and route callback kinds to `BudgetFormMixin`.
- [x] Implement the reimbursement wizard: description, positive BYN amount, payout destination/details, required photo, review/confirm; implement add-funds wizard: description, positive BYN amount, optional supporting photo, review/confirm.
- [x] Handle only private-chat Telegram photos; save the largest photo’s `file_id` in the durable draft, reject non-photo input at the receipt step, and provide `/cancel` and correction prompts for invalid amount/details.
- [x] Submit confirmed requests using existing update idempotency. Show a clear success message only after the backend confirms persistence; show the backend’s no-linked-cashier conflict without deleting the draft.
- [x] Add a cashier review keyboard: “Approve” first asks the cashier to choose cash/card; “Reject” records rejection. Call the decision endpoint using the authenticated linked cashier identity and report stale/processed requests without changing the ledger.
- [x] Extend `DeliveryWorker` and `Runtime` to poll/claim budget events, verify recipient binding generation, create persistent owner-bound callbacks, send a details message with action buttons and an optional separate photo, and ack each Telegram message ID.
- [x] For amount corrections, update all actionable detail messages with the final amount while retaining their action buttons. For a decision, remove buttons from every delivered copy and notify the applicant with final amount/status.
- [x] Reuse the current worker’s backoff and blocked-binding behavior; a Telegram delivery failure must leave the backend request and ledger state committed.

## Task 4: Website Budget and Pending Requests

**Files:**
- Create: `legacy-frontend/src/api/budget.ts`
- Create: `legacy-frontend/src/pages/budget/page.tsx`
- Create: `legacy-frontend/src/pages/budget/budget.css`
- Modify: `legacy-frontend/src/components/Header.tsx`
- Modify: `legacy-frontend/src/routes.tsx`

**Interfaces:**
- `getBudget({year?, signal?})`, `getPendingBudgetRequests({signal?})`, `updateBudgetRequestAmount({id, amount})`, `createBudgetEntry(payload)`, and `updateBudgetEntry({id, payload})` use `apiFetch`, credentials, and the shared auth headers.
- `BudgetPage` reads the current user’s `can_view_budget` capability, uses React Query for the two endpoints, and renders the server’s year/month groups without recomputing balances independently.

- [x] Add the `/budget` route under `ProtectedRoute` and a Header navigation item visible only when `currentUser.can_view_budget` is true; backend still rejects unauthorized requests.
- [x] Add BYN formatting with two decimals and cash/card balance panels at the top of the page.
- [x] Render each year as a separate group; inside it render one separate transaction table for every month, including date, income/expense, cash/card account, description, BYN amount, and authorized edit controls.
- [x] Add the separate Requests tab for pending records. Show applicant, department(s), request type, description, submitted and current amount, payout details for reimbursements, status age, and amount correction history; never show receipt/supporting images or file IDs.
- [x] Add an amount editor for authorized finance users. After save, refresh budget and request queries; show the corrected amount in the table while Telegram correction delivery runs.
- [x] Add manual income/expense creation and ledger-row edit forms for finance users; invalidate budget queries after save so all displayed monthly totals and current balances refresh.
- [x] Keep request decisions available only in Telegram; the website must not expose approve/reject controls.

## Task 5: Integration and Operator Handoff

**Files:**
- Modify: `legacy-back/docs/TELEGRAM.md` or the current backend Telegram operations documentation, only where notification configuration or behavior changes.
- Modify: `tg-bot/README.md` to document the Budget menu, request types, cashier permissions, and linked-account requirement.

- [x] Walk through a linked participant submitting a reimbursement with receipt and a separate add-funds request without photo; confirm pending data appears on the site without photo IDs.
- [x] Correct a pending amount on the site; confirm all cashier messages show the corrected amount and approval uses it.
- [x] Approve one reimbursement from cash and one add-funds request to card; confirm one expense and one income respectively, BYN balances recalculate, and the applicant receives the final result.
- [x] Reject a request and confirm no ledger row is added. Process a request from one of two cashiers and confirm the other message has no buttons; replay a stale/concurrent callback and confirm it creates no second ledger row.
- [x] Review access as a regular participant, kaznachey, chair/deputy, and admin; confirm no receipt/file ID appears in website responses or UI.
- [x] Verify the local existing `app.db` worktree change is preserved and the initial balances remain zero on a clean local database.

## Execution Notes

Tasks 1 and 2 define backend contracts used by Tasks 3 and 4, so implement those first. Bot and frontend work can then proceed in parallel only if they use the exact interfaces above. Do not modify the separate `tg-back` integration copy unless a deployment decision later requires mirroring this implementation.
