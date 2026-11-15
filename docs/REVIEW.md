# REVIEW — selective independent QA and security checklist

**Authoritative decision table:** `docs/QA_POLICY.md` (ADR-25). The old rule to launch independent QA for every `[R]` label, new SHA, retarget or stage boundary is **superseded**. Required GitHub CI and coordinator scope review apply to **every PR**. Independent QA is for **HIGH risk or material cross-PR interaction risk**, not a routine checkpoint. The reviewer must be different from the author and **read-only**.

## 1. Selective review

| Classification | Independent QA | Acceptance |
| --- | --- | --- |
| LOW: docs, plan, additive synthetic tests with unchanged runtime, CI and safety assertions | **Not required** | Current-head CI green + coordinator full effective diff check |
| MEDIUM: routine bounded non-sensitive code | **Not required by default** | Current-head CI + coordinator acceptance and targeted tests |
| HIGH: YCLIENTS API/policy/probe, auth/PII/secrets, real message recipients, sync/outbox races, money-like effects, migrations, CI/deny/security control, **QA gate weakening** | **Required** on affected risk surface | CI + independent focused PASS / REQUEST_CHANGES / BLOCKED with no unresolved BLOCKER/MAJOR |

On HIGH `REQUEST_CHANGES`, the reviewer **must** verify the original failing invariant and affected risk surface after the fix. On changed HEAD/BASE, first perform a coordinator impact analysis of the full effective patch, dependencies and CI. A material HIGH change needs targeted delta review; otherwise reuse valid prior risk evidence without a new full review. The `[R]` marker in the historical PLAN is only advisory.

A successful, verifiable final-head CI run can supply Ruff/Pyright/pytest results. Independent reviewers should prioritize targeted adversarial/negative checks; **do not run a second complete test suite by default** or claim that reused CI was independently executed.

## 2. Universal coordinator gate (every PR)

- [ ] Classify highest applicable **LOW / MEDIUM / HIGH** risk by actual behavior, not just labels, branch name or files.
- [ ] Examine full effective current HEAD-to-BASE patch, scope, acceptance criteria, conflicts, changed upstream dependencies and previous findings.
- [ ] Verify required final-head/merge CI: `uv sync --frozen && make check`, Python 3.12, Ruff, Pyright and pytest. Check `git diff --check` where available.
- [ ] Check no leak of secrets/PII, no unapproved real-world actions, new YCLIENTS routes, disablement of safety assertions, permission/CI controls, or scope creep.
- [ ] Ensure no unresolved BLOCKER/MAJOR; verify any prior independent risk finding has been explicitly closed by evidence.
- [ ] Confirm GitHub branch protection and mergeability; coordinator merges only with exact `expected_head_sha`.

## 3. Focused HIGH-risk checks (apply only if affected)

### YCLIENTS / external API
- [ ] API operations remain code-review allowlisted `kind: read`; normal runtime uses only independently validated `verified` operations. Pending candidates are restricted to authorized owner-only spike.
- [ ] Check type coercion, parsed YAML/JSON response validation **before** any HTTP/evidence, date ranges, pagination bounds, retries, status handling and malformed/oversized responses.
- [ ] Neither HTTP 200 nor a diagnostic error code proves correct branch, access rights or external schema. Sanitized evidence must be independently checked before any verified promotion.
- [ ] No raw response, token, client name, phone, headers or dynamic untrusted string leaks into logs/fixtures.

### Authorization / privacy / delivery
- [ ] `can()` in every sensitive service action and callback; permissions checked at effect time, not only preview. Contact binding accepts only own contact.
- [ ] News require `news_opt_in`; service notifications only to verified relevant participants; opt-out and late changes fail closed.
- [ ] Time and transaction semantics deterministic; UTC storage / `Europe/Moscow` domain. UNIQUE effect keys and outbox enforce idempotency; concurrent workers and retries cannot duplicate.
- [ ] Sensitive migrations and transactional writes survive crashes/replays; no new external writes or unapproved customer-facing operation.

### Security controls / governance
- [ ] No weakening of deny rules, branch protections, required CI, secret exclusion, safe routing, or independent HIGH risk review without an owner-approved ADR.
- [ ] For a **governance policy that reduces QA frequency**, an independent focused policy audit is required at initial adoption; subsequent LOW/MEDIUM changes use the adopted policy.

## 4. Report and integration

```text
T-XXX — PASS | REQUEST_CHANGES | BLOCKED
Risk surface reviewed:
HEAD and BASE / effective patch:
Verified CI (reused; not independently rerun):
Independent targeted checks actually run:
Findings [BLOCKER/MAJOR/MINOR/NIT, path:line, evidence]:
Prior findings closed:
Remaining external gates:
Merge eligibility:
```

BLOCKER/MAJOR -> REQUEST_CHANGES. Missing evidence -> BLOCKED. Low and medium PRs have **no independent report requirement**; record `QA: NOT_REQUIRED` with reason in the PR risk record. For HIGH, after a focused independent PASS and required green CI, the coordinator may automatically merge without asking the owner. A code merge never authorizes live YCLIENTS, real messages or production deployment.
