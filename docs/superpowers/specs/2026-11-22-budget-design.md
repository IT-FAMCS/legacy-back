# Budget ledger and reimbursement requests — design

Date: 2026-11-22

## Intent

Give the organization one budget ledger shared by the website and Telegram bot. Finance managers can see current cash/card balances and dated income and expense records. Any active linked participant can submit a reimbursement or an add-funds request. Cashiers review each request, and approval creates the corresponding ledger entry exactly once.

## Goals

- Add an `is_kaznachey` permission to positions, editable in the existing Positions page. Multiple positions and users may hold it.
- Add a website Budget page available to administrators, chair and deputies, and users whose position has `is_kaznachey`.
- Show current cash and card balances. Within each year, show a separate table for every month with income, expenses, descriptions, dates, and account. Start both balances at zero; do not import historical data.
- Let authorized finance users create and edit ledger transactions manually.
- Add a Telegram budget entry point for any active linked participant to submit either a reimbursement request or a request to add funds, with fields appropriate to the request type.
- Send each confirmed request to all linked, active kaznachey accounts with applicant name, department(s), type, description, amount, and applicable supporting details. A cashier can correct the pending amount, approve with a cash/card account, or reject. Notify the applicant of the result.
- Keep pending amount corrections auditable and synchronize the corrected amount to all cashier messages. Make processing atomic: once one cashier processes a request, other copies lose their action buttons and concurrent actions cannot duplicate the decision or ledger entry.
- Show pending requests in a separate website tab without receipt images. Authorized finance users can correct the amount before processing. Approval creates an expense for a reimbursement or income for an add-funds request and updates the selected balance.

## Non-goals

- Importing or reconstructing past transactions.
- Supporting several currencies, departmental budgets, spending categories, charts, or spreadsheet export in the first version.
- Approving or rejecting requests from the website. The website lists pending requests; decision buttons are in Telegram.
- Publishing receipt images on the website.

## Approaches considered

1. **One ledger and request API in the existing FastAPI application and database — selected.** The website, bot, positions, and Telegram bindings already use this backend. A single transaction can record a decision and its ledger effect, and the existing bot worker can deliver messages with retry handling.
2. **Use a spreadsheet as the financial source of truth.** This follows the current manual practice, but makes concurrent website/bot updates and one-time approvals harder to guarantee.
3. **Create a separate finance service and database.** This isolates financial code but duplicates user, department, and Telegram identity relationships and adds synchronization and deployment work.

The user selected approach 1.

## User and permission behavior

- Backend authorization is authoritative; hiding the Budget tab is only a user-interface convenience.
- Read access to the budget and pending requests is allowed to the existing executive roles (admin, chair, deputy) and kaznachey positions.
- Viewing pending requests, correcting their amount, and creating or editing ledger rows are allowed to the same finance group. Manual ledger rows may be created as income or expense, with date, account, amount, and description. Request decisions remain in Telegram.
- Any active linked participant may submit either type of request and inspect its own result through Telegram. Participants cannot read other requests.
- Kaznachey Telegram notifications and decision actions require an active identity binding and the current `is_kaznachey` permission. Permission and binding are checked again when an action is taken.

## Data and accounting behavior

- Add `Position.is_kaznachey`, default false, to the existing model, API schemas, and create/edit position form. Existing positions remain unchanged.
- Store ledger entries with direction (income/expense), account (cash/card), positive decimal amount to two fractional digits, description, operation date, creator/editor, and optional source request. A request-backed expense or income has a unique reference to its originating request. Finance users can edit the linked ledger entry after approval; this updates the actual recorded expense and recalculates the balance. The submitted amount and its correction history remain recorded. Once processed, the final request details and decision are immutable; finance users can still edit the linked ledger entry and recalculate the balance.
- Derive each current account balance from its income and expense entries; do not keep independently editable balance fields. The initial balance is therefore zero. An authorized person can record later opening corrections as ordinary dated transactions if needed.
- A confirmed request records its type, linked applicant, captured name and department(s), description, submitted amount, current amount, any amount corrections with editor and time, submission time, status, and processor/result metadata. A reimbursement also contains payout destination/details and a required receipt photo. An add-funds request has no payout destination and may include a supporting photo. Receipt and supporting photos are kept out of website responses; Telegram file identifiers are used only by the bot to send them to cashiers.
- On approval, the cashier selects the cash or card account. In one database transaction, the backend changes the pending status and creates exactly one linked entry: a cash/card expense for reimbursement or a cash/card income for add-funds. Rejection changes request status and creates no ledger row. Processed requests cannot be processed again.
- Use the project's existing SQLite schema-upgrade pattern for the new position column. New tables are created by the existing SQLAlchemy metadata initialization. Do not rewrite the existing local `app.db` contents.

## Website behavior

- Add a `/budget` route and navigation item. Only authorized users see and can open it.
- The page begins with cash and card balances, then groups records by year. Each year contains separate monthly tables showing date, direction, account, description, amount, and available edit actions.
- A separate "Requests" tab lists pending applications and their non-image details. It omits receipt and supporting photos and lets authorized finance users correct the amount before processing.
- Authorized finance users can correct pending request amounts and add or edit ledger rows, including rows linked to approved requests. The submitted amount remains recorded alongside corrections. Decisions remain in Telegram.

## Telegram behavior

- Add a "Budget" menu and a request-type choice: reimbursement or add funds. A reimbursement collects description, amount, payout destination/details, a required receipt photo, and confirmation. An add-funds request collects description, amount, an optional supporting photo, and confirmation. Cancellation or invalid input does not create a request.
- On confirmation, persist the request before notifying cashiers. Deliver one actionable message per linked, active kaznachey with the details applicable to its type and any attached photo.
- An approval action includes the cashier-selected account and creates an expense or income according to request type; rejection needs no account. The backend serializes competing decisions and returns the already-processed result to any stale callback.
- After an amount correction, refresh all pending cashier messages or send a correction notice so nobody approves a stale amount. After processing, remove decision buttons from all delivered cashier messages and notify the applicant of the final result and amount. Use durable queued delivery and retries consistent with the existing bot worker; a delivery failure must not roll back the saved request or ledger decision.

## Constraints and builder calls

- Build in the existing repositories: `legacy-back` (main backend), `legacy-frontend`, and `tg-bot`. Keep the website and bot on the same `legacy-back` API. `tg-back` is a separate integration branch/copy and is not the selected source of truth for this feature.
- Reuse the existing JWT, Telegram binding, bot-service authentication, SQLAlchemy, and Telegram worker patterns.
- Use BYN with two decimal places for entry and display, as confirmed by the user. Keep formatting centralized.
- Permit the ledger to display a negative account balance if a recorded real expense exceeds that account's current balance; do not discard or block an otherwise valid reimbursement decision. The balance display should make the negative amount visible.
- Website request details may include payout destination information for the authorized finance group, but must never include the receipt image or Telegram file identifier.
- Store timestamps in the existing UTC convention and present them in the project’s local convention (Europe/Moscow) unless the application already has a shared formatter.
- No spreadsheet example was attached in the conversation; the first version uses the fields and grouping specified above.

## Open to the builder

The implementer chooses endpoint names, exact SQLAlchemy relationship layout, bot callback token payloads, and component styling. These choices must preserve the access rules, atomic decisions, private receipt handling, and one shared ledger described here.
