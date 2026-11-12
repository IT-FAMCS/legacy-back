# Account information, warnings and equipment polish

User approved phone and education_format fields in account forms/profile/list; education
values бюджет/платное, legacy accounts can stay null. Existing account edit permissions remain.
Profile displays currently held equipment, never archived items.
Warnings: any number of active entries, mandatory issue reason; only admin, chair and chair
deputies may issue/remove, with mandatory removal reason. Student council chair, department
heads/deputies may view counts/history but cannot issue/remove. Ordinary members and
secretaries cannot see counts/history, including their own. Server enforces all restrictions.
History retains immutable reasons and actor/name/time snapshots after removal; count active only.
Separate protected warning endpoints prevent sensitive values entering ordinary user responses.
Equipment removal is restricted to admin/chair/deputies; it archives the row and preserves
custody history, hides it from inventory/profile, uses version check and confirmation modal.
Improve shared and equipment modal appearance/accessibility. Replace scale-on-hover with
paint-only, contained, reduced-motion-aware transitions; preserve controls and links.
No production DB changes during development; use additive migrations on deployment.
