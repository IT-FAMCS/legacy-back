# Telegram integration and event responsibility zones

Deploy backend, frontend and bot versions together. Backend owns identity, policies, task outbox and zone assignments. Bot never accesses the backend database directly and never receives member passwords.

Environment:
- TELEGRAM_SERVICE_KEY: shared random 32+ character secret, only on backend and bot.
- TELEGRAM_BOT_USERNAME: actual bot username without @, needed for one-time link URLs.
- TELEGRAM_NOTIFICATION_TIMEZONE=Europe/Moscow and TELEGRAM_REMINDER_HOUR=10.
- Bot-only: BOT_TOKEN, BACKEND_URL, WEBAPP_URL, BOT_DB_PATH (persistent Docker volume), NOTIFICATION_POLL_SECONDS.

Profile routes `/api/telegram/link`: GET status, POST issue, POST `/confirm` with challenge_id, DELETE revoke/cancel. Existing member JWT is required; target account IDs are not accepted. Only token hashes are stored. Claims come through the authenticated bot service and are single-use, expiring and require explicit confirmation in the issuing account.

Bot routes are under `/api/telegram/bot`: numeric actor header X-Telegram-User-ID plus X-Telegram-Service-Key. Mutations require X-Telegram-Request-ID. The current active binding/account is resolved each time. Equipment uses the exact same router/policies as the web API. Task routes expose own active list/read/completion only. Mutation receipts persist with domain changes; replays require the same request fingerprint/binding generation and current access. Service responses use no-store.

Notification queue is written atomically with task audit. It leases one delivery at a time, deduplicates reminders per task/user/deadline, retries transient errors and respects blocked chats and binding generations. There is no historical assignment backfill. External Telegram delivery is at-least-once in ambiguous transport/crash cases, not exactly-once. The bot retains a global flood cooldown and stages chat state/replies before a short atomic commit.

Event responsibility zones:
- GET/POST `/api/cards/{id}/responsibility-zones`, PATCH/DELETE `/{zone_id}`, POST `/clear`, GET `/history`.
- Writes require can_edit_cards AND card access, with board_version and zone version where relevant.
- Lead counts toward required headcount, cannot also be a helper. Only active people count toward coverage; inactive existing people stay visible. Closure requires an active lead and enough people.
- Clear removes all leads/helpers of the current event atomically, preserves zones/count requirements and records before/after history.
- Bot mirrors these routes under `/api/telegram/bot/events`; there is no profile-zone association or zone-triggered notification feature.
- Existing event deletion/recategorization is blocked while zones exist.

Validation (temporary DBs only): `python -W ignore -m unittest discover -s tests -v`.
Cross-repository chat tests live in bot `tests/integration_backend.py --backend-root ../tg-back` and use fake Telegram sends.
Browser smoke: start `tests/serve_telegram_smoke.py` (port8014) with the backend environment; build frontend using VITE_API_BASE_URL=http://127.0.0.1:4176; run `node --experimental-websocket tests/browser-telegram-smoke.mjs` from frontend. It checks claim/confirm/revoke, ownership, event-only zones, staffing/edit/clear/history, role limits and mobile. It does not open t.me or send real messages. Stop the fixture after testing. Screenshots go to `../.tools/browser`.

Every bound bot mutation also requires X-Telegram-Binding matching the current generation. The chat client sends the generation it resolved before processing a callback; a rebind between reading and writing fails closed.

If a parent event is deleted after its last zone is removed, its auxiliary board and card-ID keyed zone history are removed in the same transaction. This prevents orphan constraints and old private history attaching to a reused card ID. Task audit history is unchanged.
