# Оборудка

Approved by user: equipment tab with name, description, holder and date taken.
All active signed-in members can view equipment/history and take an item as themselves.
Only admin, chair/deputies, student council chair, department heads/deputies can
create items and manually edit item details, holder, date and shared storage values.
Secretaries and ordinary members cannot manually edit, regardless of unrelated flags.
Holder is exactly one user or storage location. Default storage: «в главке».
Search selects an active user or saved location; managers can add a new location.
User names link to /user/:login. Location has no user link.
Taking sets current member and server UTC time. Manual assignment defaults to current
UTC time for a new user; a manager can correct the date. Locations clear taken_at.
All changes of custody and corrections to taken_at append immutable history snapshots
with actor, recipient/location, taken_at and recorded_at, available in a per-item modal.
Creating an item records initial state. Editing only name/description does not fabricate a take.
Optimistic version checks reject stale changes with 409. Item change and history commit atomically.
No deletion requested. New tables are additive, existing app.db is never used by tests.
Architecture: SQLAlchemy models, focused FastAPI router wired to existing member authentication,
React Query API/page with existing navigation and styling. Server options supply can_manage.
Alternatives: free-text holder loses profile linkage; embedding inventory in cards cannot enforce
custody/history atomically. Chosen typed holders and separate tables.
