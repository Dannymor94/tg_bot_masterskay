# AGENTS.md — authoritative instructions for all project agents

`CLAUDE.md` imports this file. **ADR-24** delegates autonomous routine GitHub merges; owner-approved **ADR-25** governs risk-based QA via `docs/QA_POLICY.md` (single authoritative decision table). Do not ask the owner for ordinary GitHub, task, review or integration actions.

## 1. Context and communication

1. Read this file, locate the next eligible task in `PLAN.md`, and read only the task's **Читать / Read** references and files being changed. Read `CONTEXT.md` or `docs/DECISIONS.md` more broadly when necessary to resolve a referenced contract.
2. Treat accepted product ADRs and `docs/API_MATRIX.yaml` as authoritative; never invent YCLIENTS routes, fields, status or proof.
3. **All prompts, tasks, instructions, review requests and handoffs addressed to AI agents must be in English.** Communicate progress, decisions, blockers and owner requests in Russian. Preserve established language conventions in product text and existing source documentation where practical.
4. Use `PLAN.md` as the task tracker and keep a short up-to-date stage/status/blocker/next-step summary. A synthetic green build is not evidence of live YCLIENTS functionality.

## 2. Roles and authority

| Role | Responsibility |
| --- | --- |
| Executor — Claude Code (Sonnet 4.6) or assigned coding agent | Implement only its assigned task in its own `t-XXX-...` branch/worktree; test, commit, push, open/update PR, resolve feedback; do not duplicate a task already claimed by another executor |
| Independent reviewer — a different agent/model | Review **HIGH-risk surfaces only** and material cross-PR integration risks, publish PASS / REQUEST_CHANGES / BLOCKED; remain read-only and independent of the author |
| Senior developer + coordinator/integrator — ChatGPT | **Write code and tests directly** for eligible, unassigned and independently scoped tasks in its own branch; also maintain dependencies/tracker, verify evidence and CI, route other executors/reviews, **automatically merge eligible PRs through GitHub**, and continue to next task |
| Owner | Supplies/rotates private credentials and external-account permissions, approves materially new product ADRs, initiates owner-only real-world actions not already covered by a scoped authorization |

**Single-owner task claims (no duplicate work).** Before starting any code, check `PLAN.md`, open issues/PRs, known worktree/branch claims and the owner's latest handoff. A task the owner reports as already in progress with an agent stays with that agent **even if its branch/PR is not yet published**. ChatGPT must not duplicate, silently reassign, edit that agent's worktree or open a competing PR; choose a different safe, unclaimed task. If nothing independent is available, report the dependency instead of inventing work. ChatGPT may author and coordinate its own LOW/MEDIUM PRs, but must still perform a separate, evidence-based final diff/CI inspection; for HIGH, an **independent reviewer different from the author** remains mandatory. No code-authoring role changes the external owner gates or `docs/QA_POLICY.md`.

**No recurring merge permission prompts.** The owner's standing authorization covers routine task branches, PRs, comments, retargeting, QA handoffs, status updates, merges and post-merge cleanup **subject to the gates in §3**. A GitHub branch-protection rule is never bypassed.

## 3. Autonomous task-to-merge loop

1. Reconcile task ownership **before** selection, including owner-reported agent work not yet visible on GitHub. Select the first unblocked **unclaimed** `AGENT` task with completed dependencies. ChatGPT may implement it directly as senior developer or delegate it to Claude Code. When HUMAN/live work blocks progress, choose a different independent unclaimed task; otherwise record the blocker without duplicate implementation or repeated prompts.
2. Create/use a dedicated `t-XXX-...` branch and worktree. Give a concise 3–5-line plan identifying touched files, tests and acceptance criteria; begin without waiting for approval. Split diffs above approximately 400 lines into traceable subtasks.
3. Add or update regression tests, implement only scoped changes, run `uv sync --frozen && make check` and `git diff --check`. Do not weaken tests or CI to achieve green. If checks fail, fix them automatically and rerun.
4. Update the relevant `PLAN.md` task status and, only with separately verified evidence, necessary matrix/config contracts. Commit `T-XXX: <description>` and ordinary `git push -u origin HEAD` **from the own task worktree only**. Open/update the PR; attach exact head/base SHAs, diff scope, test results and limitations. If push fails, report the actual blocker; never claim publication.
5. Classify the actual PR effect as **LOW / MEDIUM / HIGH** using `docs/QA_POLICY.md`; record the reason, diff, head/base, CI and decision. **LOW and MEDIUM: coordinator scope/acceptance inspection + green CI, no separate QA. HIGH: one independent read-only risk-surface review before merge.** A historical `[R]` marker alone is not a gate. An end-of-stage review is required **only if** multiple workstreams introduce material interaction risk; batch it into one focused review.
6. Resolve BLOCKER/MAJOR findings automatically in the task branch. New SHA/base/retarget **always** needs coordinator incremental/effective diff + dependency + CI impact inspection, **not** automatic independent QA repetition. If a HIGH risk-surface or its dependencies materially changed, ask only for a **targeted independent re-review** of the changed invariant and old blockers. Reuse otherwise applicable independent evidence and verified final-SHA CI; no repetitive independent full-suite runs. Do not discard an unresolved major finding.
7. **Integrator automatically merges a clean, eligible PR** after checking actual effect, dependencies, complete current diff, required final-head/merge CI, the `docs/QA_POLICY.md` tier-specific evidence, no unresolved BLOCKER/MAJOR, no secrets/PII or unapproved change, and branch protection/mergeability. Use the exact GitHub `expected_head_sha` guard. **Do not ask the owner for routine merge permission.**
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
- No direct pushes to `main`, force pushes, `git reset --hard`, unauthorized history rewrites, branch/tag deletion, cross-worktree edits or bypass of CI/**required risk-based** independent QA. Executor does not locally `git merge` main; coordinator uses GitHub PR merges.
- No undocumented dependencies, suppressed validation, or silent user-visible behavioral changes. No new write endpoints in YCLIENTS.
- Pure domain logic in `app/domain/`; time as a parameter and table-driven tests. Side effects use UNIQUE `dedupe_key`, Telegram only through outbox, thin handlers, `can()` in each service action, UTC database / `Europe/Moscow` domain, numbers in `app/config/studio.yaml`, texts in `app/texts/ru.py`. Respect existing ADRs.
- A change to these security limits is **not** an ordinary autonomous maintenance task: record a separate explicit owner-approved architectural/security decision.

## 6. Available commands and execution reality

`make setup`, `make check`, `make test`, `make run`, `make migrate`, `make health`, `make spike-plan` (offline). **Owner-gated:** live `make spike`, `make invite`, production actions as described above.

The dispatcher and Runner are **not deployed in this project** (ADR-23). Until an external orchestrator is actually connected, agents do not awaken themselves. ChatGPT is also the senior developer: during an active request it can implement safe, unclaimed code/tasks directly, execute available GitHub integration steps, delegate distinct tasks with precise English instructions, and report actions that could not be performed. An existing agent's in-flight assignment is never silently taken over. Do not claim 24/7 or background execution without a working automation.

## 7. Evidence and reporting

Every task handoff/report includes: task and acceptance criteria, files and PR diff, worktree/branch, base+HEAD SHAs, explicit **LOW/MEDIUM/HIGH** classification and scoped QA decision, CI evidence, independent reviewer status **only if required**, push/merge state, blockers/unknown external evidence, and next eligible task. The owner's Russian dashboard shows completed/total **formal** PLAN tasks, current stage, milestones, blockers, owner-only gates and next action. Do not present arbitrary implementation activity as a new completed top-level task.
