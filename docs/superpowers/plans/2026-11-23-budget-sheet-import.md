# Budget monthly totals and Google Sheets import

Goal: show income, expense and net movement in each month's collapsible heading, and provide an admin-only import button for a public Google workbook with Статья/Доход/Расход columns.

Observed source: gid=0 contains summary balances, while June–November 2026 operations live in named monthly sheets. Section headings and subtotal/final rows must not become ledger entries. Source contains no exact dates or authoritative cash/card column. Import all operation sheets; derive month from sheet title and use the first day of that month (Moscow time) when no date exists. Require the administrator to choose the account; optional fallback month handles undated sheets whose titles lack a month. Explain these choices in the preview.

- [x] Add Decimal monthly income_total, expense_total, net_total derived from visible ledger entries. Preserve totals after corrections/deletion and expose admin-derived can_import flag.
- [x] Build a bounded Google-only read-only fetcher and pure CSV parser: title/header discovery, Russian month/year parsing, comma/NBSP amounts, optional explicit dates, skip summaries and categories, reject ambiguous/invalid money rows without partial import.
- [x] Add durable preview batch and source-row mapping tables. Preview never creates entries; commit is admin-only, serialized, atomic, expiring and replayable. Stable spreadsheet+sheet+row+direction keys prevent duplicate imports, including deleted entries. A changed previously imported source row is a conflict requiring manual correction rather than silently duplicating money.
- [x] Add an admin-only button with URL/account/fallback-month form, review of parsed rows and totals, then commit. Keep income/expense/net visible when a month is collapsed.
- [x] Tests cover source-layout parsing, monthly arithmetic, authorization before network access, invalid URLs/data, preview/commit/replay/concurrency/expiry, deleted/changed source rows, and frontend control visibility. Read the live workbook only to validate parser output; never import it into app.db or production automatically.
- [x] Full test suites, lint/build, browser smoke, independent review, commit and push to current main/master branches under the standing user instruction.


## Continued scope (user instructions)
- Separate Treasurers tab, cash and card per person, manual current balances with durable adjustment audit and stale-balance protection.
- Every operation has nullable editable treasurer_id, independent of immutable creator. Manual creation defaults to actor; processed requests use processor. Reassignment moves personal holdings; null counts only in global ledger. Legacy operations are backfilled once from creator during upgrade.
- Ruling: continue in current working directories to preserve started tests and existing app.db, as requested by “continue”.
- Ruling: personal balance adjustments affect custody only, not the operation ledger; explain possible difference in tab. Non-treasurer former holders remain visible to avoid losing their balances.
- Live source read-only parser check: 7 sheets, 94 operation directions, MAIN skipped. Exact dates absent in named month sheets.


## Final verification and review
- Budget backend suite: 35 tests OK (import, custody, reassignment/null, processor attribution, concurrent import, expiry, replay, migration interruption and explicit null preservation).
- Frontend: 36 tests OK; TypeScript/Vite build and ESLint OK. Existing Node/Vite version and bundle-size warnings remain.
- Live Google workbook read-only parse: 7 sheets; 94 operation directions; MAIN skipped. No live entries imported.
- Independent read-only review: /root/budget_review. Fixed row-direction identity, source-only fingerprint, missing description rejection, explicit account choice and visible selected holder/account, admin alias, atomic migration, current actor/current holder option preservation, active-select filtering and stale balance editor recovery.
- Final: Ruling: this importer appends source rows and does not synchronize removals. Changed imported rows block subsequent import; UI accurately instructs restoring source and editing ledger manually. Explicit reconciliation/versioning is outside this feature; cost is manual repair if a previously imported source row is changed.
- Final: Ruling: use focused budget backend suite and frontend checks, skip full backend/browser reruns under user's explicit instruction to finish faster and avoid unnecessary tests. Remaining limitation: no browser end-to-end validation of new dialogs this session.
- app.db SHA256 D22ACA01F9025BC362257070AA9E9918F46845D8919E62BEA329DE44750180ED unchanged throughout; its preexisting working-tree modification is excluded from commits.
