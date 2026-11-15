@AGENTS.md

## Additional instructions for Claude Code

- One independently scoped task per session; use `/clear` between unrelated tasks.
- Apply `.claude/settings.json` deny controls as a second safety layer; do not circumvent them. The primary guard remains keeping actual secrets outside this repository.
- After green `make check`, commit and ordinary `git push -u origin HEAD` **only from your dedicated task branch/worktree**. Open/update the task PR with exact HEAD/BASE, CI, full scope and risk classification; send for **independent QA only when HIGH/material integration risk**, not after every trivial SHA change.
- **Do not ask the owner for routine GitHub operations.** ADR-24 authorizes autonomous reviewed PR merges; ADR-25 and `docs/QA_POLICY.md` define LOW/MEDIUM/HIGH gates. CI is always required, while independent QA is only for HIGH/material integration risk. The coding executor must not bypass required QA or push to `main`.
- Do not start live YCLIENTS probes, real Telegram invitations/messages, or production deploys without the documented external authorization gate.
- Do not add `@` imports pointing outside the repository.
- Write all agent task/handoff instructions in English; owner-facing progress is in Russian.
