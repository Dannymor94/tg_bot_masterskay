"""Owner-only, read-only YCLIENTS live spike (T-103b).

Fail-closed by default: `make spike` (no authorization flag) exits before
reading secrets or touching the network. The owner alone authorizes and runs
the live probe. This script NEVER promotes an operation to ``verified`` and
NEVER edits ``docs/API_MATRIX.yaml``; a successful probe only emits a proposal
for separate independent review. It prints safe operation ids, candidate
method and HTTP status/error category only -- no response body, no field
values, no secrets, no headers.

Live probing reuses the code-reviewed T-102 ``YclientsClient``: every route is
restricted to the static ``SAFE_READ_ROUTES`` allowlist, responses are
diagnostic-redacted, and evidence is written shape-only (see T-103a).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path
from typing import Literal, TypedDict

from pydantic import JsonValue

from app.integrations.yclients import YclientsClient
from app.integrations.yclients_policy import (
    AdapterError,
    ApiMatrix,
    OperationDenied,
    OperationUnavailable,
    load_matrix,
)
from app.settings import Settings, load_settings
from scripts.spike_preflight import safe_fixture_document

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "yclients"
MAX_REQUESTS = 50  # hard request budget across the whole spike; fail closed if exceeded
CONFIRM_PHRASE = "READ LIVE YCLIENTS"
GATE_MESSAGE = (
    "spike BLOCKED: live read-only YCLIENTS probe is owner-gated.\n"
    "No secrets were read and no request was made.\n"
    "The owner, after independent QA, runs it manually, e.g.:\n"
    "  uv run --frozen python -m scripts.spike_readonly --authorize-live-read\n"
    "Add --probe-phone to privately test clients.search_by_phone.\n"
)


@dataclass(frozen=True)
class ProbeSpec:
    needs_dates: bool = False
    needs_page: bool = False
    is_phone_search: bool = False
    skip_reason: str | None = None


# Only static, safe probes. record.get needs a concrete id from a verified
# records.list, so it is not probed here; the phone search is probed only with
# an explicit, privately entered test phone (otherwise left unavailable).
PROBE_SPECS: dict[str, ProbeSpec] = {
    "company.get": ProbeSpec(),
    "staff.list": ProbeSpec(),
    "services.list": ProbeSpec(),
    "records.list": ProbeSpec(needs_dates=True, needs_page=True),
    "activities.search": ProbeSpec(needs_dates=True),
    "record.get": ProbeSpec(skip_reason="needs a record_id from a verified records.list"),
    "clients.search_by_phone": ProbeSpec(is_phone_search=True),
}

Category = str


class AttemptReport(TypedDict):
    candidate_index: int
    method: str
    category: Category


class OperationReport(TypedDict):
    operation_id: str
    status_before: str
    attempts: list[AttemptReport]
    probed_ok: bool
    proposed_candidate_index: int | None
    note: str | None


class SpikeReport(TypedDict):
    mode: Literal["live-owner-spike"]
    verified_promoted: Literal[False]
    matrix_modified: Literal[False]
    needs_independent_approval: Literal[True]
    company_scope_bound: bool
    requests_made: int
    operations: list[OperationReport]


def _categorize(error: AdapterError) -> Category:
    if isinstance(error, OperationUnavailable):
        return "unavailable"
    if isinstance(error, OperationDenied):
        return "denied"
    if error.status_code is None:
        return "no_response_or_malformed"
    return f"http_{error.status_code}"


async def _probe_once(
    client: YclientsClient,
    operation_id: str,
    candidate_index: int,
    values: dict[str, str | int],
    body: dict[str, JsonValue] | None,
) -> JsonValue:
    return await client.request(operation_id, values, candidate_index=candidate_index, body=body)


async def run_spike(
    *,
    client: YclientsClient,
    matrix: ApiMatrix,
    output_dir: Path,
    now: datetime,
    phone: str | None = None,
    budget: int = MAX_REQUESTS,
) -> SpikeReport:
    """Probe each static candidate once; write shape-only evidence on success.

    Never promotes to ``verified`` and never edits the matrix. ``phone`` (if
    given) is used only in-memory for the phone search and never written.
    """
    reports: list[OperationReport] = []
    requests_made = 0
    probe_date = now.date().isoformat()
    page_size = matrix.meta.page_size or 0

    for op in sorted(matrix.operations, key=lambda item: item.id):
        if op.kind != "read":
            raise OperationDenied
        spec = PROBE_SPECS.get(op.id, ProbeSpec(skip_reason="no probe plan"))
        if spec.skip_reason is not None:
            reports.append(
                {
                    "operation_id": op.id,
                    "status_before": op.status,
                    "attempts": [],
                    "probed_ok": False,
                    "proposed_candidate_index": None,
                    "note": f"not_probed: {spec.skip_reason}",
                }
            )
            continue
        if spec.is_phone_search and phone is None:
            reports.append(
                {
                    "operation_id": op.id,
                    "status_before": op.status,
                    "attempts": [],
                    "probed_ok": False,
                    "proposed_candidate_index": None,
                    "note": "unavailable: no private test phone supplied (use --probe-phone)",
                }
            )
            continue

        base_values: dict[str, str | int] = {}
        if spec.needs_dates:
            base_values["from"] = probe_date
            base_values["to"] = probe_date
        if spec.needs_page:
            base_values["page"] = 1
            base_values["count"] = page_size

        attempts: list[AttemptReport] = []
        probed_ok = False
        proposed_index: int | None = None
        for index, template in enumerate(op.candidates):
            method = template.split(" ", 1)[0]
            if requests_made >= budget:
                raise AdapterError  # request budget exhausted; fail closed
            values = dict(base_values)
            body: dict[str, JsonValue] | None = None
            if spec.is_phone_search and phone is not None:
                if method == "POST":
                    body = {"phone": phone}
                else:
                    values["phone"] = phone
            requests_made += 1
            try:
                raw = await _probe_once(client, op.id, index, values, body)
            except AdapterError as error:
                attempts.append(
                    {"candidate_index": index, "method": method, "category": _categorize(error)}
                )
                continue
            attempts.append({"candidate_index": index, "method": method, "category": "ok"})
            _write_evidence(output_dir, op.id, raw)
            probed_ok = True
            proposed_index = index
            break  # first safe success is the proposal; stop probing this op

        reports.append(
            {
                "operation_id": op.id,
                "status_before": op.status,
                "attempts": attempts,
                "probed_ok": probed_ok,
                "proposed_candidate_index": proposed_index,
                "note": None if probed_ok else "no candidate returned a safe success",
            }
        )

    company_ok = any(r["operation_id"] == "company.get" and r["probed_ok"] for r in reports)
    return {
        "mode": "live-owner-spike",
        "verified_promoted": False,
        "matrix_modified": False,
        "needs_independent_approval": True,
        "company_scope_bound": company_ok,
        "requests_made": requests_made,
        "operations": reports,
    }


def _write_evidence(output_dir: Path, operation_id: str, raw: JsonValue) -> None:
    """Persist a shape-only artifact (no values, no names, no ids-as-values)."""
    document = safe_fixture_document(operation_id, raw)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{operation_id}.json"
    path.write_text(
        json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_client(settings: Settings, matrix: ApiMatrix) -> YclientsClient:
    from app.settings import load_studio

    return YclientsClient(
        base_url=str(settings.yclients_api_host),
        partner_token=settings.yclients_partner_token,
        user_token=settings.yclients_user_token,
        company_id=settings.yclients_company_id,
        policy=load_studio().yclients_http,
        matrix=matrix,
        allow_pending=True,  # owner spike may try reviewed candidates of pending read ops
    )


async def _run_live(settings: Settings, *, phone: str | None, output_dir: Path) -> SpikeReport:
    matrix = load_matrix()
    async with _build_client(settings, matrix) as client:
        return await run_spike(
            client=client, matrix=matrix, output_dir=output_dir, now=datetime.now(UTC), phone=phone
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-only live read-only YCLIENTS spike")
    parser.add_argument("--authorize-live-read", action="store_true")
    parser.add_argument("--probe-phone", action="store_true")
    parser.add_argument("--no-confirm", action="store_true", help="skip the interactive prompt")
    parser.add_argument("--output-dir", type=Path, default=FIXTURES_DIR)
    args = parser.parse_args(argv)

    # Fail closed BEFORE reading secrets or networking.
    if not args.authorize_live_read:
        parser.exit(status=2, message=GATE_MESSAGE)
    if sys.stdin.isatty() and not args.no_confirm:
        if input(f"Type '{CONFIRM_PHRASE}' to run the live probe: ").strip() != CONFIRM_PHRASE:
            parser.exit(status=2, message="spike aborted: confirmation did not match\n")

    settings = load_settings()
    phone = (
        getpass("Private test phone (not stored): ").strip() or None if args.probe_phone else None
    )
    try:
        report = asyncio.run(_run_live(settings, phone=phone, output_dir=args.output_dir))
    finally:
        phone = None  # drop the in-memory phone promptly
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
