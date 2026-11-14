# AGENTS.md — authoritative instructions for all project agents

`CLAUDE.md` imports this file. **ADR-24** supersedes the former owner-only merge rule in ADR-20/22/23. This project uses an **autonomous, evidence-gated workflow**: do not ask the owner for ordinary GitHub, task or integration actions.

## 1. Context and communication

1. Read this file, locate the next eligible task in `PLAN.md`, and read only the task's **Читать / Read** references and files being changed. Read `CONTEXT.md` or `docs/DECISIONS.md` more broadly when necessary to resolve a referenced contract.
2. Treat accepted product ADRs and `docs/API_MATRIX.yaml` as authoritative; never invent YCLIENTS routes, fields, status or proof.
3. **All prompts, tasks, instructions, review requests and handoffs addressed to AI agents must be in English.** Communicate progress, decisions, blockers and owner requests in Russian. Preserve established language conventions in product text and existing source documentation where practical.
4. Use `PLAN.md` as the task tracker and keep a short up-to-date stage/status/blocker/next-step summary. A synthetic green build is not evidence of live YCLIENTS functionality.

## 2. Roles and authority

| Role | Responsibility |
| --- | --- |
| Executor — Claude Code (Sonnet 4.6) or assigned coding agent | Implement one eligible task in its own `t-XXX-...` branch and isolated worktree; test, commit, push, open/update PR, resolve feedback |
| Independent reviewer — a different agent/model | Inspect pinned head/base and diff, independently execute checks for `[R]` tasks and stage review, publish PASS / REQUEST_CHANGES / BLOCKED; never approve its own changes |
| Coordinator/integrator — ChatGPT or designated integrator | Maintain dependencies/tracker, verify evidence and CI, route fixes/reviews, **automatically merge eligible PRs through GitHub** and continue to next task |
| Owner | Supplies/rotates private credentials and external-account permissions, approves materially new product ADRs, initiates owner-only real-world actions not already covered by a scoped authorization |

**No recurring merge permission prompts.** The owner's standing authorization covers routine task branches, PRs, comments, retargeting, QA handoffs, status updates, merges and post-merge cleanup **subject to the gates in §3**. A GitHub branch-protection rule is never bypassed.

## 3. Autonomous task-to-merge loop

1. Select the first unblocked `AGENT` task with completed dependencies. When waiting on a HUMAN/live dependency, select another independent eligible task; record the blocker, do not spin or ask repeatedly.
2. Create/use a dedicated `t-XXX-...` branch and worktree. Give a concise 3–5-line plan identifying touched files, tests and acceptance criteria; begin without waiting for approval. Split diffs above approximately 400 lines into traceable subtasks.
3. Add or update regression tests, implement only scoped changes, run `uv sync --frozen && make check` and `git diff --check`. Do not weaken tests or CI to achieve green. If checks fail, fix them automatically and rerun.
4. Update the relevant `PLAN.md` task status and, only with separately verified evidence, necessary matrix/config contracts. Commit `T-XXX: <description>` and ordinary `git push -u origin HEAD` **from the own task worktree only**. Open/update the PR; attach exact head/base SHAs, diff scope, test results and limitations. If push fails, report the actual blocker; never claim publication.
5. For `[R]`, security/privacy/auth/API, synchronization, money-like rules, changes to safety gates, or changed integration bases: obtain **independent reviewer PASS on the exact head/base pair**. A reviewer must be read-only, and the author cannot sign off on its own code. For ordinary non-`[R]` tasks, green CI + coordinator diff/acceptance review permits merge; also conduct independent end-of-stage review.
6. Resolve BLOCKER/MAJOR findings in the task branch, rerun CI and repeat independent QA on the **new exact SHA**. MINOR/NIT can be deferred only if the reviewer explicitly deems them nonblocking. Retargeting, rebasing, changes to main or resolution of merge conflicts require checking the **new base/diff** and repeating independent review whenever the earlier sign-off no longer covers the pair.
7. **Integrator automatically merges a clean, eligible PR** when: dependencies are satisfied; `make check`/required GitHub checks pass on the final SHA; all required independent reviews PASS on the final head/base; no unresolved BLOCKER/MAJOR, secrets, PII, unapproved ADR deviation, out-of-scope change or conflict; and branch protections permit it. Verify the exact head SHA immediately before calling GitHub's PR merge endpoint; use the expected-head guard. **Do not ask the owner to approve this routine merge.**
8. Verify merge SHA and resulting `main` state, reconcile/close completed Issues, update `PLAN.md` in an appropriate follow-up PR if needed, refresh the tracker, and automatically select the next eligible task. Do not delete other agents' branches or force-update their work. Never mark a task done merely because a PR or review exists.

Before every commit/push: inspect `git rev-parse --show-toplevel`, `git branch --show-current`, `git status -sb` and `git diff --cached --name-only`; verify the right repository/worktree, scope and absence of credentials/PII. Verify clean status and published SHA after push. Executors never push directly to `main`; integrators merge **only via reviewed PRs**, not by bypassing checks.

## 4. External authority and escalation — exceptional, not routine

**Automatically allowed without asking:** tasks already authorized by PLAN/ADR; branches/worktrees, code and doc edits, synthetic tests, CI, PRs, review requests, retries/fixes, accepted PR merges, labels/issues/tracker updates, nonproduction local builds, and deterministic fallback for operations marked `unavailable`.

**Retained owner/external gates:** acquiring, inspecting or rotating real secrets; changing YCLIENTS/account permissions; **initiating a new live YCLIENTS probe** (`make spike` and any authorized real API request); `make invite` and real Telegram/customer-facing sends outside an already authorized workflow; purchasing services, legal/consent changes and actions affecting real people or money; an initial production deployment or material change to a deployed environment without an already approved, rollback-tested release workflow; substantive product ADR changes or expansion beyond PLAN §7. A narrowly scoped standing owner authorization can cover subsequent repeated operations **within that exact documented scope**, without requesting approval every time.

When such a gate is encountered, complete all safe preparatory work, document a single clear decision/input request for the owner, and continue any independent task. Do **not** infer new live-action permission from permission to merge code. Never ask for tokens, real client data or screenshots of credentials in chat/Issues.

## 5. Safety and product invariants

- YCLIENTS is the read-only source of truth (ADR-01). Normal runtime may use only `kind: read` **verified** operations in `docs/API_MATRIX.yaml`; the owner-only spike is an explicitly gated exception for reviewed `pending` read candidates. `unavailable` uses its documented fallback.
- Do not read, print or commit secrets; secrets stay outside the repo. No `printenv`, `env`, `docker compose config`, `docker inspect`, or reading private files outside the repo. No raw API payloads in logs, reports or fixtures; avoid names/phones from YCLIENTS per ADR-06. `redact()` is a diagnostic boundary, not a verified DTO.
- Never invent API paths, permissions, response shapes, `verified` evidence, customer data or test results; use independently verified sanitized fixtures and scoped code-reviewed routes.
- No direct pushes to `main`, force pushes, `git reset --hard`, unauthorized history rewrites, branch/tag deletion, cross-worktree edits, or bypass of CI/independent QA. Executor does not locally `git merge` main; coordinator uses GitHub PR merges.
- No undocumented dependencies, suppressed validation, or silent user-visible behavioral changes. No new write endpoints in YCLIENTS.
- Pure domain logic in `app/domain/`; time as a parameter and table-driven tests. Side effects use UNIQUE `dedupe_key`, Telegram only through outbox, thin handlers, `can()` in each service action, UTC database / `Europe/Moscow` domain, numbers in `app/config/studio.yaml`, texts in `app/texts/ru.py`. Respect existing ADRs.
- A change to these security limits is **not** an ordinary autonomous maintenance task: record a separate explicit owner-approved architectural/security decision.

## 6. Available commands and execution reality

`make setup`, `make check`, `make test`, `make run`, `make migrate`, `make health`, `make spike-plan` (offline). **Owner-gated:** live `make spike`, `make invite`, production actions as described above.

The dispatcher and Runner are **not deployed in this project** (ADR-23). Until an external orchestrator is actually connected, agents do not awaken themselves. The coordinator executes available GitHub steps during an active request, prepares precise English tasks for the independent executor/reviewer, and reports any actions that could not be performed. Do not claim 24/7 or background execution without a working automation.

## 7. Evidence and reporting

Every task handoff/report includes: task and exact acceptance criteria, files and PR diff, worktree/branch, base+HEAD SHAs, exact independent QA status when required, `make check` and test count, push/merge state, blockers/unknown external evidence, and next eligible task. The owner's Russian dashboard shows completed/total **formal** PLAN tasks, current stage, milestones, blockers, owner-only gates and next action. Do not present arbitrary implementation activity as a new completed top-level task.
