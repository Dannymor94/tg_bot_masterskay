# REVIEW — independent acceptance and integration checklist

**Applies to:** every `[R]` task, any security/privacy/auth/API/sync or safety-gate change, any new head/base pair whose previous sign-off is invalid, and one independent review of accumulated ordinary tasks at the end of each stage. The reviewer must be different from the author and **read-only**. Follow `AGENTS.md` and ADR-24.

## Evidence first

- [ ] Confirm exact repository, HEAD SHA, BASE SHA, task requirements and complete diff; do not trust the author's report alone.
- [ ] Reproduce `uv sync --frozen && make check` on Python 3.12 in a clean isolated worktree; capture Ruff/Pyright/pytest results and `git diff --check`.
- [ ] Verify touched files, dependencies, acceptance criteria, tests and no out-of-scope feature expansion.
- [ ] For a retarget/rebase or moved main, compare the full new base-to-head patch against previously approved evidence. Filename counts alone are insufficient. Require a new explicit verdict on the current head/base pair.
- [ ] Inspect safe handling of exceptions/logs, known parsing bounds and error cases; no secret or PII leaks.

## YCLIENTS

- [ ] Only code-reviewed `kind: read` routes from `SAFE_READ_ROUTES`; normal runtime requires `verified`. Only an owner-authorized `make spike` may inspect reviewed pending read candidates.
- [ ] API routes and schema derive from `API_MATRIX.yaml` plus independently verified sanitized fixtures. 200 or error code 15 alone proves neither data semantics nor permission cause.
- [ ] Fixtures and reports contain no client names, phones, tokens, headers, raw bodies, free-text error messages or unapproved identifiers.
- [ ] Unknown/unavailable operations use documented fallback; no silent promotion to `verified`.

## Authorization, privacy and reliability

- [ ] No real secrets in code/tests/logs. No YCLIENTS names/phones persisted except the temporary own-contact R4 exception per ADR-06.
- [ ] `can()` is enforced in each service action, including callbacks; clients/teachers cannot access someone else's records or phones. Contact binding accepts only the requesting user's own contact.
- [ ] News require `news_opt_in`; service notices go only to relevant participants, and unsubscribe is available.
- [ ] Deterministic domain functions, tabular tests, injected time, UNIQUE effect keys, Telegram only via outbox, UTC persistence / `Europe/Moscow` domain.
- [ ] Replay and restart do not duplicate tasks/messages; test races, Telegram 429, blocked bot, cancellation after reminder, blank/partial API responses and rollback.
- [ ] “Cannot come” creates R2, never writes to YCLIENTS. Room occupancy combines room-column records and group activities with that room resource.
- [ ] No change to deny rules, permissions, secret handling, production behavior or ADR without corresponding authorization and evidence.

## Verdict and automatic integration

`BLOCKER`: secrets/PII, unauthorized real action, unverified API, unsafe write, broken acceptance. `MAJOR`: logic/reliability error or missing critical test. `MINOR` and `NIT` are nonblocking only when explicitly classified as such.

```text
T-XXX — PASS | REQUEST_CHANGES | BLOCKED
Reviewed HEAD:
Reviewed BASE:
Independent checks:
Findings: [severity] path:line — evidence → required fix
Scope/limits:
Recommended next step:
```

BLOCKER/MAJOR => REQUEST_CHANGES; missing evidence => BLOCKED. A current independent PASS, clean PR and required green CI make an in-scope change **eligible for autonomous coordinator merge**, without a separate owner request. Reviewer does not merge or edit the code. **Code merge never authorizes a live YCLIENTS probe, production release or customer-facing action.**
