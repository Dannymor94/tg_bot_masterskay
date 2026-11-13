# T-203 task queue: standalone reference helpers (NOT production)

Three small pure decision points for the T-203 agent:

- `task_key(rule, source_key)`: deterministic JSON-encoded, collision-resistant-by-structure keys for R1–R6 and R8. `source_key` must be stable and **never contain PII**. Use the real event/transition identity from the relevant source, not current time/UUID. This does not replace the database UNIQUE constraint.
- `reminder_due`: only a pending (`open`) task during Moscow studio working hours, no earlier than `overdue_ping_minutes` after creation or last ping. All numbers and hours are parameters loaded from `studio.yaml`. **Interpretation to confirm against task design:** elapsed minutes continue overnight; a due overnight reminder becomes eligible next business opening. The existing `tasks` DB table has **no `created_at` field**. The T-203 agent must explicitly supply a trustworthy persisted creation time (with a reviewed migration if needed) rather than inventing the timestamp. A proper scheduler/outbox must separately enforce durable keys, quiet hours, permissions and transaction boundaries. Merely checking this predicate does not enqueue a reminder.
- `should_clear_closed_text`: after the configured `done_ttl_days`, redact body/title of a completed task, not the task row or its audit/dedupe identity. Do not erase open R4 data prematurely.

Also includes advisory `can_take` and `can_reply` helpers. These do **not** implement permissions or atomicity. T-203 MUST keep service-level `can()` using fresh DB permissions and use conditional SQLite `UPDATE ... WHERE state='open'` under transaction.

The reference code imports only stdlib and does not define any YCLIENTS fields, Telegram callback format, real credentials or production DB schema. Agents may adapt the logic, but **must not import `scaffolds/` from the running bot** or assert T-203 completed from these snippets. Resolve any disagreement with ADR-13 and `docs/CONTRACTS.md`, cover the full lifecycle with synthetic integration tests, and run `make check` on the integrated branch.

Local standalone test: `python -m unittest discover -s tests -p 'test_task_queue_scaffold.py' -v`.
