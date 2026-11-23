Plan: docs/superpowers/plans/2026-11-22-budget-ledger.md
Task 1: in progress
Ruling: Use each repository default branch: legacy-back main, legacy-frontend master, legacy-tg main. The backend and bot repositories have no master branch; using their default branches keeps the changes on the requested primary line. Cost if wrong: code lands on main rather than a literal master branch in those repositories.
Ruling: No automated tests will be added or run because the developer instruction prohibits tests unless the user asks. Manual review/verification will be used where possible. Cost if wrong: defects that automated tests might catch can remain.
app.db was already modified before implementation and must remain untouched/uncommitted.

2026-11-23 resumed: prior no-tests ruling superseded by current instructions; financial behavior covered with isolated automated tests. Working in existing repositories as previously selected. Task 2 RED: four API tests fail with 404 before implementation. Approval uses expected_amount to reject stale cashier buttons after corrections.


## Completed 2026-11-23

Task 1: complete — permission flag, backward-compatible idempotent schema upgrade, five models, current-user capabilities and Positions UI. Migration tested against an old temporary schema. Existing app.db SHA256 D22ACA01F9025BC362257070AA9E9918F46845D8919E62BEA329DE44750180ED unchanged throughout this continuation.
Task 2: complete — finance-only ledger APIs, strict positive finite bounded decimal amounts, pending corrections/history, service/binding authenticated bot APIs, replay receipts, atomic decisions and durable leased outbox.
Task 3: complete — reimbursement/add-funds wizard, photo handling, draft retention, stable per-draft submission key, current cashier decisions/corrections, all-cashier synchronization and applicant notification.
Task 4: complete — protected Budget page/navigation, BYN balances, separate year/month tables, pending requests without photos, manual create/edit and correction forms, cache hidden and navigation capability removed on 403.
Task 5: complete — documented joint deployment and cashier setup; API + real worker integration verifies two cashiers, receipt sends, correction, single decision, cleanup and applicant outcome. Reimbursement cash expense and card add-funds income verified. Processed requests immutable after manual ledger corrections.

Pre-flight interfaces: backend Position/UserResponse flags consumed by frontend; backend request/entry DTOs consumed by frontend and bot; notification claim/validate/progress/final ack consumed by worker. Shared API paths and field names verified by cross-repository integration.

Final review: independent reviewer /root/budget_review, read-only review of actual files/diffs and spec. Important findings corrected in one fix pass:
- all-cashier correction delivery barrier and canonical-only actions — test_approval_waits_until_all_cashier_corrections_are_delivered RED→GREEN.
- durable passive-first detail delivery and progress checkpoints — test_initial_message_has_no_buttons_before_durable_progress_ack and test_delivery_progress_is_durable_and_blocked_cashier_is_not_available RED→GREEN.
- blocked bindings no longer count as reachable cashiers — progress/blocked regression RED→GREEN.
- pre-send permission/lease/binding validation; demotion cleanup removes markup only — test_pre_send_validation_revokes_sensitive_payload_on_role_loss RED→GREEN.
- stale finance cache hidden after 403 — frontend permission revocation test RED→GREEN.
- submission retry after proxy failure reuses draft flow key — test_confirm_retry_after_proxy_failure_reuses_draft_idempotency_key RED→GREEN.
- standalone backend test portability — optional cross-repository test skips when sibling bot clone is absent. Other backend tests remain standalone; integration ran here.
- photo retries cannot delay processed keyboard removal — test_failed_photo_retry_does_not_block_processed_keyboard_removal RED→GREEN.

Final: Ruling: preserve existing working directories/branches: legacy-back main, legacy-frontend master, tg-bot feat/telegram-integration. Earlier note describing the bot branch as main was inaccurate for the selected tg-bot worktree. No branch switches, commits, pushes or deployment performed. Cost if wrong: integration into other branches is still required.
Final: Ruling: add validate/progress acknowledgement and expected_amount approval guard to the planned interfaces. Required to prevent actionable orphan messages and stale approvals during retries/corrections. Cost if wrong: backend and bot must be deployed together; mismatched versions cannot deliver finance events.
Final: Ruling: current automated-test instructions supersede the previous session's no-tests decision; financial and permission behavior required tests. Cost if wrong: extra local validation time; no production effects.
Final: minor (deferred): parsers accept insignificant extra decimal zeros (1.2300 becomes 1.23). Nonzero excess precision is rejected; financial values remain exact to two places.
Final: minor (deferred): no-cashier bot error is general; precise cashier flag/link/unblock recovery steps are in docs/TELEGRAM.md and tg-bot/README.md.

Final validation:
- legacy-back: venv Python unittest discover -s tests -q: 97 tests OK; 12 budget tests included, cross-repository delivery test ran.
- tg-bot: Python 3.13 unittest discover -s tests -q: 45 tests OK; four budget bot regressions included.
- legacy-frontend: npm test: 29 tests OK; npm run lint exit 0; npm run build exit 0. Existing Vite warnings about local Node 20.15.1 and bundle size remain.
- Chrome browser-budget-smoke: PASS admin/cashier access, member denied, year/month groups, negative balance, create/edit and recalculation, pending correction/history, receipt privacy, phone layout, zero runtime exceptions.
- Browser fixture used a disposable DB on localhost:8016 and was stopped afterwards. Browser temp profile cleaned by test. Final dist rebuilt with ordinary project configuration after the smoke test.
- Screenshots: H:/Наследие/.tools/browser/budget-desktop.png and budget-requests-mobile.png.
- git diff --check clean in all three repositories. Existing app.db remains modified exactly as it was before this continuation, uncommitted.


## Publication requested 2026-11-23

User accepted normalization of insignificant trailing decimal zeros; this is no longer a deferred issue. User requested no notice about missing cashiers; request confirmation now uses a neutral retry message and retains its draft, without naming cashier availability. Regression test observed RED before changing the response text. User explicitly authorized push of the completed feature. Changes will be committed without app.db or generated logs; bot main will receive a fast-forward from the existing feature worktree after verification.
