# Budget usability and member search implementation plan

**Goal:** Delete budget operations with recalculation, show operation authors/applicants, collapse monthly groups, attach the existing animated raccoon to approved outcomes, and search members using familiar names.
**Architecture:** Extend the existing finance API and page. Soft-delete operations with deleted_at/deleted_by so the request link and decision history remain intact; hidden operations contribute nothing to balances. Return finance-authorized names/logins and links. Reuse the existing racoon-loading.gif as a bundled bot asset and upload using Telegram multipart sendAnimation. Rejections remain text. Member search runs locally on the already authorized, department-filtered user list, using a deterministic Russian-name alias dictionary.
**Constraints:** Preserve app.db, all finance access checks and decision idempotency. No extra notices about cashier availability. Keep numeric normalization. User's prior instruction to push completed work remains active.

- [x] Backend: tests for manual/request-linked deletion, balance recalculation, repeated delete and unauthorized access; identity fields without receipt IDs; schema upgrade. Implement tombstone columns and migration, finance DELETE endpoint, identity DTOs and exclusion of removed entries.
- [x] Frontend: tests for names/profile links and monthly details; add delete confirmation and query refresh. Add author/applicant column and independently expandable months, initially open.
- [x] Bot: tests for approved GIF, rejected plain text, long-caption handling and multipart upload. Bundle the existing GIF in the Docker image and send approval animation with result caption; preserve long descriptions by sending them separately when needed.
- [x] Member search: tests for Михаил/Миша, common aliases, ё/е, casing, surname+nickname in either order, login and no matches. Add normalized local matching, search input, clear control and empty-result hint while retaining department/status filters and sorting.
- [x] Verify full suites, lint/build, browser actions and asset validity. Independent review, commit and push all three repositories; bot main fast-forward only if its working copy is clean.


## Verification and review

All functional changes implemented. Independent review by /root/budget_followup_review found partial animation delivery replay, stale destructive confirmations, repeated author queries and keyboard focus outside deletion confirmation. These were reproduced and fixed:
- expected_updated_at now guards deletion; changed live rows return 409, refresh and require confirmation again. Existing tombstone retries remain idempotent.
- approved-animation message ID is checkpointed in its own BudgetNotification.payload; long-description retries do not resend a successful GIF.
- ledger creator/editor/request/applicant are eagerly loaded; 15 request-linked entries require fewer than 10 SELECTs instead of 20.
- delete confirmation uses native modal dialog with keyboard containment, Escape cancellation and focus restoration.

Search uses an explicit alias dictionary, including ambiguous nicknames for both genders, normalizes case and ё/е, and requires every query term. It retains department/active-state filtering and sorting. This is nickname matching, not typo correction or external AI lookup.

Choices: deletion retains a tombstone and original request link for audit/idempotency; names are shown with profile links, with the original captured applicant name preserved. Months start expanded and toggle independently. The bundled raccoon GIF is copied unchanged from the existing site asset, avoiding third-party dependencies at delivery time. Long approval descriptions are sent separately after the caption, with durable progress.

Checks: backend budget regression suite 17 tests OK; bot full suite 48 tests OK; frontend 32 tests OK, lint and build OK. Extended Chrome smoke passed delete/cancel/recalculation, focus/Escape, month collapse/reopen, author profiles, nickname+surname/login search, no-match feedback, clear, existing finance authorization, receipt privacy and mobile layout. GIF structure and multiple animation frames validated without new dependencies. Final backend full suite: 102 tests OK. Existing app.db SHA256 D22ACA01F9025BC362257070AA9E9918F46845D8919E62BEA329DE44750180ED unchanged; test server stopped; ordinary frontend build restored.
