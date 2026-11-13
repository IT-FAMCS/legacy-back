# Card tasks

Tasks share one record across department and event card tabs and `/tasks`.
Departments are existing cards in «Отделы и направления», events are cards in «Мероприятия»; the profile department directory is unrelated. General tasks may have neither link. A department's general group contains its tasks without an event.

All members with access to every linked card can create/edit/complete/reopen. Only chair/deputy positions can archive tasks; administrator alone does not bypass this rule. Accountless trainees have no task API. Archive keeps history and remains viewable with the same access checks. Audit snapshots contain actor/time/before/after; snapshots referencing currently inaccessible cards are redacted.

The task API lives in `tasks.py`, new tables in `models.py`; `create_all` adds tables to existing databases. No reset or data import is required. Card deletion, category movement and task linking serialize through a DB transaction to preserve links. Referenced cards/categories cannot be deleted or recategorized while referenced (including archived tasks). Task history preserves titles and assignment snapshots.

PATCH and DELETE require a task version; stale requests return 409. The editor presents latest data after a conflict and reapplies only locally changed fields when explicitly requested. Lists refresh after mutations and every 30 seconds.

Future Telegram notifications should use stable task/account IDs and the committed task history/version as a source of changes. No Telegram message sending, scheduler, token or notification service is implemented.

## Validation
Run from backend worktree using an existing environment with requirements installed:
`..\legacy-back\venv\Scripts\python.exe -W ignore -m unittest discover -s tests -v`

Run frontend `npm test`, `npm run lint`, `npm run build`.

Browser integration uses a fresh isolated server on every run; no production/local app.db is touched:
1. Run `tests/serve_tasks_smoke.py` from the backend worktree (port 8013).
2. From frontend, set `VITE_API_BASE_URL=http://127.0.0.1:4175` and run `npm run build`.
3. Run `node --experimental-websocket tests/browser-tasks-smoke.mjs` with Chrome installed (or set CHROME_BIN).
4. Stop the fixture after the run. Screenshots are written to `../.tools/browser/tasks-*.png`.

The test covers shared task identity in both card tabs, general tasks, editing other users' tasks, complete/reopen, audit, conflict draft recovery, deputy-only archive, and real mobile page/editor width.

Profile task lists use assignee_id and active_only query parameters, in addition to existing card access and archive filtering. The UI calls these records таски and labels archive actions explicitly.
