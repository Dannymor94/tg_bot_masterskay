# Parallel development plan: ChatGPT and Claude Code

**Status:** T-102 independent repair in progress. This file is a plan, not proof of QA or completed tasks. The owner receives planning and ETA summaries in Russian; agent instructions remain in English.

| Workstream | Lead | Prerequisite | Effort estimate | Exit gate |
| --- | --- | --- | --- | --- |
| T-102 adapter hardening | ChatGPT (code author), independent QA (review) | T-101 merged | 3–6 engineer-hours | Own branch + draft PR, clean Python 3.12 independent `make check` |
| T-203 and T-301 integration | Claude Code | Separate [R] approvals of #11 and #12, owner merge of first PR | 4–9 engineer-hours | Issue #14 cross-feature tests, no R4 phone leak, [R] |
| T-103 API contract discovery | Agent writes spike; only owner runs it with real credentials | Approved T-102, owner prerequisites T-001 | 3–6 engineer-hours, excluding external wait | anonymized fixtures, verified/unavailable matrix and explicit authorization |
| T-104 record sync | Claude Code | T-103 verified fixtures | 8–16 engineer-hours | deterministic sync and independent QA |

## Operating rules

- One task per independent worktree/branch; never edit another agent's branch.
- Coding agents implement, test, commit, push and open draft PRs themselves.
- ChatGPT authors independent code where that prevents critical-path blocking, but its own changes require separate QA and owner merge.
- Review agents independently inspect exact SHA and re-run `uv sync --frozen && make check`; an environment blocker is not a code failure.
- Only owner authorizes merge/deploy, secret usage, changes to ADR and live YCLIENTS execution.
- Existing `T-102 / PR #3` remains REQUEST_CHANGES; new repair PR will supersede it without changing its branch.
- Estimates are ranges of **total labor**, not guaranteed wall-clock completion.
