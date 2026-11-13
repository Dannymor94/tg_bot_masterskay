# Issue #14 — T-203 + T-301 integration [R]

## Source and scope

- Fresh base: origin/main `436fd69c3406b67181fa4df2ad4d80bd13f65380`,
  after the owner's merge of PR #16 / T-102b.
- Accepted T-203: `e6e7aaedc1aa7dc612243cfcc4a74090a7dc3dee` (PR #12).
- Accepted T-301: `8d799b2a8d00ca089b6b459a4dea83b4be5fa292` (PR #11).
- Dedicated branch/worktree: `t-203-301-integration` / `tg-bot-issue14-integration`.
  Each source commit was cherry-picked; no upstream PR was merged or changed.
- Decomposition: source commits retain T-203a–d / T-301a–d; integration fixes,
  cross-feature regressions and review handoff are separate commits.

## Semantic integration

Shared files were combined by behavior: both sets of `can()` actions; access,
task and client router registration with callback updates; T-102 redaction/error
texts plus both features' texts; dynamic task rendering plus serialized client
reply_markup in TelegramSender; both contracts and PLAN entries. Invite deep
links remain handled before ordinary onboarding. The delivery retry, cooldown,
403 and quiet-hours machinery is unchanged.

R4 creation uses the shared task rule/create_task boundary. Task, phone-free
identity, reminder timestamp and task.card notifications commit with onboarding
state; there is no parallel R4 notification producer. The outbox contains task IDs,
never a saved phone/card body. Cards render under the sender's write lock using
current roles, task state and contents.

## R4 lifecycle (ADR-06/09/13)

The explicitly preferred Issue #14 flow is implemented: unresolved R4 remains
open, with administrator-only Retry linking. Generic Take/Done are denied. An
attempted Take enqueues a current card with Retry instructions and its button;
no phone is discarded and no assignee transition occurs. Only verified binding
closes R4 and clears the phone, with client ID, state, audit and notification in
one transaction. Failed lookup leaves R4 open. No shadow PII storage or ADR change.

Two inherited R4 transition expectations in test_tasks now assert this deliberate
contract instead of the incompatible Take-and-erase behavior. Other inherited
scenarios are preserved; the new cross-feature suite verifies successful binding
and erasure after denied Take, including ordinary administrator and owner paths.

## Validation

`uv sync --frozen && make check`, Python 3.12.14: **PASS, 816 tests**.
Ruff format/check and Pyright: passed. Output: `Issue-14-check.txt`.

New regression coverage: denied Take/Done followed by failed/successful Retry;
two admins racing Take/Retry on separate DB connections; real TelegramSender
consent/news/contact and task keyboards; R4 reminders; pending card revocation
and post-binding phone erasure; source notification failure rollback including
kv identity; combined router invite/onboarding coexistence; malformed and
unprivileged callbacks; functional authorized task callback. Existing T-102b,
T-105, T-201, T-203 and T-301 tests run together.

Independent [R] must use a separate clean checkout of the published final SHA,
run `uv sync --frozen && make check`, and inspect the diff against the base and
both accepted sources. Verdict and exact SHA belong in the draft PR conversation;
this implementation report does not claim independent acceptance.

## Limits / next action

Runtime ClientLookup remains unavailable until T-103 verifies fixtures. All
lookup and Telegram transports in tests are synthetic/mocked. No live APIs,
credentials, merge or deploy. Telegram's existing crash-after-acceptance duplicate
delivery limitation is unchanged. Next: independent QA, then owner merge decision.
PR #11/#12 stay open until the integration is accepted and merged.
