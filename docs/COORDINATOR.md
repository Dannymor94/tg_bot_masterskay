# COORDINATOR — autonomous project integration and review protocol

The repository is authoritative. **ADR-24** replaces the former owner-only merge approval in ADR-20/22/23. The coordinator must maximize autonomy **without** treating its own work as independently reviewed. No dispatcher or Runner is assumed to exist until actually deployed.

## Responsibilities

1. Recover the current work state from `PLAN.md`, exact GitHub PR/Issue/commit SHAs, `docs/API_MATRIX.yaml`, `app/config/studio.yaml`, relevant ADRs and independently observed results. Never treat agent summaries as proof.
2. Pick the next unblocked task and write all instructions to agents **in English**. Summarize decisions, progress and owner-only needs **in Russian**.
3. Organize separate executor/reviewer branches, independent `[R]` reviews per `docs/REVIEW.md`, reproducible tests, and targeted fix requests. When authorship overlaps, assign a **different** independent reviewer.
4. Advance the task/PR autonomously; do not ask the owner to approve an ordinary issue, branch, PR, retarget, merge, or routine documentation update.
5. After every meaningful step, update a concise owner-facing tracker: formal completed/total PLAN tasks, active stage, accepted PRs, blockers, next autonomous action, and any true owner-only input. Distinguish QA acceptance from merged code, and merged code from verified live functionality.

## Deterministic merge gate — no routine owner question

The coordinator/integrator may automatically merge a PR via the GitHub PR merge endpoint only if **all** of these hold:

- The task is in scope, prerequisites are satisfied and it does not need a new material product/security ADR, account permission or external-action grant.
- The PR has a current stable HEAD and BASE SHA, a reviewed complete diff, no secret or client-data exposure, and is cleanly mergeable under existing branch-protection rules.
- `uv sync --frozen && make check` and required GitHub CI checks are green for the exact HEAD (or the exact merge candidate where required).
- For `[R]`, privacy/security/API/auth/sync changes, or an altered base, **independent read-only QA explicitly covers the exact current head/base**; no BLOCKER/MAJOR remains. Ordinary non-`[R]` changes require green checks and a coordinator diff review, plus an independent review at the stage boundary.
- A retarget/rebase/merge conflict or head movement triggers a fresh diff examination and independent re-review when previous approval no longer covers the patch; no outdated PASS reuse.
- The integrator re-fetches the PR and uses `expected_head_sha`. No force push, direct main push, history rewrite or bypass of checks.

On success: verify the resulting merge SHA and main tree; update/close relevant Issues, reconcile `PLAN.md` in a follow-up PR as needed, and select the next unblocked task. On failure: record the evidence, automatically fix safe blockers and retry; ask the owner only if a true external authority gate or irreconcilable decision is encountered.

## Owner-only gates (minimize interruption)

Owner/external permission is still required for: real YCLIENTS probes and credentials; YCLIENTS scope/access changes; starting real Telegram messages or invitations outside an already authorized operational workflow; payments and legal consent; a new production deployment without an existing approved and recoverable deployment workflow; substantive product ADR changes and feature expansion outside `PLAN.md`.

A standing owner permission can authorize a **specific scope of repeated external operations**; do not ask again within that scope. Authorization to merge code is **not** authorization to call live APIs or deploy to customers. Never request tokens, private customer records or secret-file contents in chat.

If a HUMAN step blocks the critical path, do all safe offline tasks in parallel; communicate one concrete owner action and its reason. Do not create unverifiable tasks or claim an unattended agent loop exists.

## Evidence

| Activity | Required evidence |
| --- | --- |
| Independent `[R]` | Pinned head/base, diff, clean independent `make check`, security/PII boundary, signed PASS/REQUEST_CHANGES/BLOCKED |
| End-of-stage review | Full stage diff, tasks, tests, independent review for unreviewed ordinary tasks |
| Merge | Clean PR, current head/base, required CI and QA, guarded GitHub merge result, confirmed new main SHA |
| T-103/YCLIENTS | Sanitized owner-run evidence, independently validated response schema/pagination/branch, no implicit `verified` promotion |
| Escalation | Precise blocker, smallest required external action, safer fallback, and next executable work |

A code-review PASS approves only the reviewed scope. For HTTP 403, a syntactically documented endpoint or numeric diagnostic code does not prove the root cause.

## Independent review format

```text
T-XXX — PASS | REQUEST_CHANGES | BLOCKED
Head SHA:
Base SHA:
Checks independently run:
Findings (severity, path:line, evidence, minimal fix):
Merge eligibility:
Remaining external gates:
Next task:
```

For a fix request, supply one self-contained **English** agent instruction per task, with exact file lines and acceptance tests. Executors fix/commit/push their own task branch; the coordinator, **not the executor**, performs reviewed PR merges. An independent reviewer never edits the reviewed source.
