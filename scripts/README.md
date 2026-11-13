# T-103a — offline preparation (not a live YCLIENTS API spike)

This module does not make network requests, read credentials, or change API_MATRIX.yaml.

Run make spike-plan to print a deterministic manifest containing all seven
declared read-only operation IDs, reviewed HTTP method/route templates,
required placeholders and current verification states. The plan does not prove
that external endpoints exist or return a particular schema. Unreviewed route
templates are denied by the T-102 code allowlist.

fixture_shape and safe_fixture_document accept only in-memory JSON and return
shape-only data. No original scalar values survive, unknown dynamic dictionary
keys are discarded, and list sampling/recursion are bounded. These structural
samples are NOT proof of a verified API schema.

The live T-103 spike remains human-gated by PLAN.md and AGENTS.md. Before any
owner-authorized live calls, T-001 and T-102 must be complete, the actual
spike program must pass its own review, and owner must authorize execution
with real read-only credentials. Never put client phones in shell history.

After any owner-authorized probe, a separate reviewer must validate safe
evidence, pagination semantics, normalized fields, fallback logic and the
proposed API_MATRIX.yaml + studio.yaml changes. A successful HTTP request alone
must never promote a route to verified. Never commit personal data/credentials.

Use Python 3.12 uv sync --frozen && make check for final independent QA.
