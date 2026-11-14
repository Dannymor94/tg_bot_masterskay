@AGENTS.md

## Additional instructions for Claude Code

- One independently scoped task per session; use `/clear` between unrelated tasks.
- Apply `.claude/settings.json` deny controls as a second safety layer; do not circumvent them. The primary guard remains keeping actual secrets outside this repository.
- After green `make check`, commit and ordinary `git push -u origin HEAD` **only from your dedicated task branch/worktree**. Open/update the task PR and hand off exact HEAD/BASE for review.
- **Do not ask the owner for routine GitHub operations.** The coordinator/integrator is authorized by ADR-24 to merge eligible PRs automatically after required CI and independent QA. The coding executor must not bypass the coordinator/reviewer or push to `main`.
- Do not start live YCLIENTS probes, real Telegram invitations/messages, or production deploys without the documented external authorization gate.
- Do not add `@` imports pointing outside the repository.
- Write all agent task/handoff instructions in English; owner-facing progress is in Russian.
