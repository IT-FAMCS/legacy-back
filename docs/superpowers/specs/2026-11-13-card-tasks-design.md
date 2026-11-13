# Tasks in department and event cards

## Purpose and agreed behavior
Add a Tasks tab to cards in «Отделы и направления» and «Мероприятия». The existing content remains in its own tab. «Общая информация» has no task tab.

A task has a title, description, server-generated creation time, optional deadline, several responsible accounts, several existing department/direction cards, and zero or one event card. A task is a single shared record: all its department and event views reflect the same updates and completion state.

Department views show expandable event groups containing task tables, plus a General tasks group for tasks without an event. Event views show tasks linked to that event. Everyone with access can create, edit, complete and reopen tasks, including tasks authored by other people. Only the chair and deputies may delete tasks. History is available on each task.

## Approach
Use dedicated task tables and API with links to existing cards and user accounts. Reuse authentication, card access checks, existing UI colors and React Query. Alternatives: markdown checklists cannot support shared structured records or audit reliably; independent copies in each card would diverge. Dedicated shared records are the proposed implementation.

## Builder decisions
- Department/event choices use category identity and current card records, not the account department directory.
- Tasks may have no department or event (global general tasks). Context is preselected in card creation forms; changing links is allowed. A dedicated Tasks page has three group modes: departments, events, and general tasks without either link, all using expandable groups and tables. Department general tasks have a department but no event. Tasks with no links are available to all authenticated members. Every list respects linked card access rules.
- Deadline is an optional calendar date. Overdue means an unfinished task with deadline earlier than today, displayed in local calendar time.
- Multiple responsible accounts are supported; unassigned tasks are allowed. New assignments offer active accounts; existing assignments survive deactivation.
- Task access requires access to its linked event when present and to all linked departments. This conservative rule prevents exposing restricted event/task details through another card. Linking inaccessible cards is rejected. Accountless trainee sessions have no task access.
- Deletion means archive: remove from active views while preserving the task and full history. History records actor, timestamp, action and before/after field values, including assignment and link changes. Deleted tasks remain available in a separate archived view for users who can access them; only chair/deputies can archive, without an implicit administrator bypass.
- UI: accessible Content/Tasks tabs; task counts and progress; create/edit dialog; deadline and status indicators; responsive tables; filters for status and responsible person; per-task history panel. Completed tasks remain visible and can be reopened. Avoid changing shared markdown behavior.
- Every update and audit event commits atomically. Stable IDs, updated time and version checking prepare the domain for future bot notifications without implementing bot delivery, scheduled jobs or credentials.
- Existing databases gain additive tables only; production and current working databases are not used for tests.

## Implementation boundaries and validation
Backend: models, schemas, router and additive startup setup; role enforcement and card authorization on reads/writes/history; validation of referenced categories/accounts; audit snapshots and conflict handling.
Frontend: API/types, task tab component, dedicated Tasks route and navigation, shared query invalidation and integration into the card page. Tests cover cross-card shared state, authorization, deletion roles, archive history, invalid links, assignment changes, completion/reopen, and concurrent edits. Run backend suite, frontend tests, lint and build; inspect desktop and narrow layouts.

## Out of scope
Telegram notifications, recurring tasks, subtasks, files, kanban, cross-app dashboards other than the requested Tasks page. Document future bot integration around stable task IDs and change history rather than implementing it now.


## Linked card lifecycle
Block deleting a department/event card or moving it to another category while any task (including archived tasks) references it. Return a readable conflict requiring users to move task links first. Never silently unlink tasks or turn a restricted task into a globally visible one. Renaming within the category preserves stable ID links. Task audit snapshots preserve the original titles independently of later card renames.
