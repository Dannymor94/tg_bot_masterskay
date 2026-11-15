# QA_POLICY — risk-based evidence and autonomous PR integration

**Authoritative policy** for QA scheduling and merge eligibility under owner-approved ADR-25. `AGENTS.md`, `docs/REVIEW.md`, `docs/COORDINATOR.md` and PLAN `[R]` labels defer to this decision table. **Do not start an independent reviewer by default.** CI and an evidence-based diff decision remain mandatory.

## 1. Classify by effect, not label or file alone

Record the **highest** applicable risk in the PR. A historical `[R]` label, new commit SHA, retarget or stage boundary does **not** by itself require independent QA. Classify the *behavior and trust boundary*, not just the list of changed paths or whether the author calls the change "tests only".

| Tier | Scope / examples | Minimum gate | Independent reviewer |
| --- | --- | --- | --- |
| **LOW** | Editorial docs, tracking, comments, cosmetic refactor provably without behavior changes, **additive synthetic tests only** without changing production code, fixtures, test configuration, safety assertions or CI | Required CI green, full effective diff + acceptance review by coordinator | **No** |
| **MEDIUM** | Bounded routine feature/bug fix affecting only local non-sensitive logic, UI or internal formatting; new harmless modules behind verified existing boundaries | Required CI green, coordinator diff review + targeted automated tests | **No by default**; escalate if uncertain, interacting workstreams, or material end-of-stage integration |
| **HIGH** | YCLIENTS route/parser/DTO/permission/probe, secrets/PII, auth/RBAC, recipient selection or Telegram delivery, money-like effects, outbox/sync idempotency and races, migrations or data loss, external network/write boundaries, CI/deny/security or **QA-governance weakening**, production safety/rollout changes | Required CI green, focused regression/negative tests, **independent read-only risk-surface review** | **Yes, before merge** |

Overrides: unsafe operations, actual leaked secrets/PII, unverified API promotion, unapproved architecture changes, failing required CI, unresolved BLOCKER/MAJOR or branch conflicts are **never auto-mergeable**. Uncertain classification goes to HIGH; do not de-risk by splitting a change, renaming a task, or combining a safety modification with test-only files. A tests-only diff that **removes/weakens** a security or authorization assertion is HIGH. Changes to security/PII policy or external permissions remain owner-gated in addition to HIGH QA.

## 2. CI once per candidate, independent checks only where useful

1. **Every PR** must have green required GitHub CI on its final head/merge candidate: Python 3.12, `uv sync --frozen && make check` (Ruff format/lint, Pyright, full pytest). Verify `git diff --check`, changed files, no secrets/PII, and requirements. Do not merge on a failing, missing or stale required check.
2. The coordinator performs **one full effective diff/acceptance inspection** for LOW/MEDIUM. No reviewer Issue, QA chat, independent full suite or generic sign-off ritual merely because the PR is new.
3. For HIGH, request **one independent review of the changed risk surface**: relevant code paths, attack/failure modes, missing assertions, privacy/security boundaries, existing open findings. The independent reviewer may rely on **verified, successful final-SHA CI results** for Ruff, Pyright and the full test suite, rather than running the entire suite again. Independently execute **targeted negative/proof tests** when CI cannot prove a boundary. Do not call an author's claims independent evidence.
4. At stage completion, review only a **material cross-PR integration risk** (e.g. jointly changed authorization, record-to-recipient flow or transaction semantics), batching it as **one targeted independent review**. Do not require a full independent end-of-stage review for a purely LOW/MEDIUM stage with green integration CI and no interaction risk.
5. On HIGH `REQUEST_CHANGES`, fix the issue, obtain green CI on final SHA and ask the independent reviewer to re-examine the **original failing invariant and directly affected code**; reuse still-applicable prior risk evidence. Full repeated independent `make check` is **not** required when the final SHA's complete CI is independently verifiable. Never silently turn a prior MAJOR into PASS.
6. A LOW/MEDIUM fix after ordinary CI failure needs CI rerun and coordinator inspection, **not** an independent reviewer unless its actual risk tier increases.

## 3. SHA/base changes — impact analysis, not automatic QA resets

- For **any new HEAD**, the coordinator checks the incremental diff, final CI and whether scope, security assumptions or earlier evidence changed. If **only LOW changes** are added to an already reviewed HIGH patch and the HIGH code, tests, dependent contracts and behavior are unchanged, prior independent coverage remains usable after documenting this comparison. Material HIGH changes require **targeted independent delta re-review**.
- After **main advances / retarget / rebase**, inspect the actual resulting patch and changed upstream dependencies, conflicts and required integration CI. If the reviewed HIGH implementation, its dependencies/contracts and risk semantics are unchanged, **reuse the existing independent verdict**; do not demand a new review simply because BASE SHA changed. If material effects or dependencies changed, get a focused independent re-review of the new risk surface.
- **Never infer patch equivalence** from filenames, test count or a new base SHA alone. Record exact SHAs, structural diff and any dependency/contract changes. Failing CI or merge conflict blocks integration regardless of earlier PASS.
- Never rewrite history or force-push to avoid an old review. When the evidence no longer applies, block only that PR and continue unblocked work elsewhere.

## 4. Single automatic merge gate

The **coordinator/integrator** autonomously merges a PR through GitHub if: task is in scope, dependencies satisfied, the final exact head/base and complete effective diff have been checked, required checks green, any HIGH independent QA resolved with no BLOCKER/MAJOR, branch protections and mergeability pass, no external-authority violation, and the merge request uses `expected_head_sha`. No routine owner approval or status questions. After merge verify merge SHA/main, reconcile completed tasks, select the next unblocked task.

The coding agent may push only its own task branch. The independent HIGH reviewer must be different from the author and **read-only**, and must not merge. For LOW/MEDIUM, the coordinator's scoped diff inspection + CI are sufficient; do not label these as "independently QA-approved".

## 5. Required short PR risk record

```text
Risk: LOW | MEDIUM | HIGH (highest applicable; reason and affected boundary)
HEAD / BASE:
Effective changed files and behavior:
Required CI run / result:
Open BLOCKER/MAJOR:
Independent QA: NOT_REQUIRED (why) | PASS (review URL and covered risk) | PENDING
Base/HEAD impact: unchanged risk evidence | targeted re-review (why)
External gates: none | precise owner-only action
Merge: eligible | blocked (reason)
```

For a HIGH independent report: `PASS / REQUEST_CHANGES / BLOCKED`, pinned reviewed change and risk surface, tests/probes actually executed vs verified CI that was reused, findings with severity and paths, limitations and remaining owner gates. Revalidate evidence when the delta materially changes; the verdict is not a perpetual blanket pass.

## 6. Non-negotiable external boundary

Even a merged LOW/MEDIUM/HIGH PR never authorizes reading real credentials, changing YCLIENTS permissions, a **new live YCLIENTS read probe**, real Telegram/client-facing deliveries, spending money, legal/consent changes, or a new production deploy without an already approved recoverable workflow. YCLIENTS `API_MATRIX.yaml` remains `pending` until separately authorized, independently validated live evidence justifies a reviewed promotion. T-103 stays open and T-104 blocked until then. No unattended Runner/dispatcher is implied by this process file.

**Adoption:** ADR-25 changes the QA scheduling process only. The first governance PR adopting this reduction is HIGH **under ADR-24** and needs an independent policy/safety review before merge; subsequent routine LOW/MEDIUM changes follow this document.
