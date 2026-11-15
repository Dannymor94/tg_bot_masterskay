@AGENTS.md

## Additional instructions for Claude Code

- One independently scoped task per session; use `/clear` between unrelated tasks.
- Apply `.claude/settings.json` deny controls as a second safety layer; do not circumvent them. The primary guard remains keeping actual secrets outside this repository.
- After green `make check`, commit and publish via `bash scripts/push_task_branch.sh` **from your dedicated task-worktree root**. The no-argument wrapper verifies the current `t-XXX` branch, approved GitHub `origin` and clean tracked tree, then runs `git push -u origin HEAD`. Open/update the task PR with exact HEAD/BASE, CI, full scope and risk classification; send for **independent QA only when HIGH/material integration risk**, not after every trivial SHA change.
- Shared `.claude/settings.json` preapproves **only** `Bash(bash scripts/push_task_branch.sh)`, not raw or wildcard `git push`. Run the literal command at the assigned worktree root with no extra arguments (never a `cd ... &&` wrapper), after verifying repository, task ownership, staged scope, current HEAD and `origin`. The script must fail closed on `main`, a non-task/detached branch, wrong remote or tracked changes. No force push, direct `main` push, branch-protection/CI bypass or safety-rule edits. Higher-priority managed/local `ask` or `deny` and tool sandbox permissions still apply: if blocked, stop and report the exact permission source; do not evade the block or ask the owner to paste a routine command as a workaround.
- **Do not ask the owner for routine GitHub operations.** ADR-24 authorizes autonomous reviewed PR merges; ADR-25 and `docs/QA_POLICY.md` define LOW/MEDIUM/HIGH gates. CI is always required, while independent QA is only for HIGH/material integration risk. The coding executor must not bypass required QA or push to `main`.
- Do not start live YCLIENTS probes, real Telegram invitations/messages, or production deploys without the documented external authorization gate.
- Do not add `@` imports pointing outside the repository.
- Write all agent task/handoff instructions in English; owner-facing progress is in Russian.
