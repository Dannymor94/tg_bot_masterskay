"""Offline YCLIENTS route preflight and privacy-safe fixture shape inspection.

This does not make HTTP requests, read secrets, grant verification or run T-103.
A human must explicitly authorize any later live spike after T-001/T-102.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from string import Formatter
from typing import Literal, TypedDict

from pydantic import JsonValue

from app.integrations.yclients_guards import SAFE_DIAGNOSTIC_KEYS, safe_read_route
from app.integrations.yclients_policy import ApiMatrix, load_matrix


class PreflightError(ValueError):
    """Static evidence does not justify execution or acceptance."""


class CandidatePlan(TypedDict):
    method: str
    template: str
    required_parameters: list[str]
    read_only_allowlisted: bool
    api_verified: bool


class OperationPlan(TypedDict):
    operation_id: str
    current_status: str
    candidates: list[CandidatePlan]
    requires_human_evidence: bool
    auto_promoted: bool


class PreflightPlan(TypedDict):
    mode: Literal["offline-preflight"]
    network_access: Literal[False]
    secret_access: Literal[False]
    confirms_external_api: Literal[False]
    operations: list[OperationPlan]


# These keys are safe schema labels, not verified YCLIENTS response fields.
# Personal names/phones and identifiers are never included as values.
SHAPE_KEYS = SAFE_DIAGNOSTIC_KEYS
MAX_DEPTH = 12
MAX_LIST_SAMPLES = 3


def _route_fields(template: str) -> tuple[str, ...]:
    try:
        method, location = template.split(" ", 1)
        if method not in ("GET", "POST") or not location.startswith("/"):
            raise PreflightError("unapproved route syntax")
        fields: set[str] = set()
        for _, name, spec, conversion in Formatter().parse(location):
            if name:
                if spec or conversion or not name.isidentifier():
                    raise PreflightError("invalid placeholder syntax")
                fields.add(name)
        return tuple(sorted(fields))
    except (ValueError, IndexError) as error:
        raise PreflightError("invalid route template") from error


def make_preflight(matrix: ApiMatrix) -> PreflightPlan:
    """Return a deterministic, non-sensitive planning manifest, never API proof."""
    operations: list[OperationPlan] = []
    seen: set[str] = set()
    for op in sorted(matrix.operations, key=lambda item: item.id):
        if op.id in seen or op.kind != "read":
            raise PreflightError("non-read or duplicate operation")
        seen.add(op.id)
        candidates: list[CandidatePlan] = []
        for template in op.candidates:
            if not safe_read_route(op.id, template):
                raise PreflightError("candidate not present in reviewed allowlist")
            method, _ = template.split(" ", 1)
            candidates.append(
                {
                    "method": method,
                    "template": template,
                    "required_parameters": list(_route_fields(template)),
                    "read_only_allowlisted": True,
                    "api_verified": False,
                }
            )
        if op.status == "verified":
            if op.path is None or not safe_read_route(op.id, op.path):
                raise PreflightError("verified route is not code allowlisted")
        elif op.status not in ("pending", "unavailable"):
            raise PreflightError("unexpected operation state")
        operations.append(
            {
                "operation_id": op.id,
                "current_status": op.status,
                "candidates": candidates,
                "requires_human_evidence": op.status == "pending",
                "auto_promoted": False,
            }
        )
    return {
        "mode": "offline-preflight",
        "network_access": False,
        "secret_access": False,
        "confirms_external_api": False,
        "operations": operations,
    }


def fixture_shape(value: JsonValue, *, _depth: int = 0) -> JsonValue:
    """Produce a shape-only fixture; no original scalar values survive.

    Only static safe key labels are retained. Unknown keys are silently
    discarded, since a dictionary key itself may contain a phone or name.
    A list has at most three illustrative item shapes, not its original length.
    """
    if _depth >= MAX_DEPTH:
        raise PreflightError("fixture exceeds maximum depth")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise PreflightError("fixture keys must be strings")
        return {
            key: fixture_shape(value[key], _depth=_depth + 1)
            for key in sorted(value)
            if key in SHAPE_KEYS
        }
    if isinstance(value, list):
        return [fixture_shape(item, _depth=_depth + 1) for item in value[:MAX_LIST_SAMPLES]]
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, int):
        return "<int>"
    if isinstance(value, float):
        return "<float>"
    if isinstance(value, str):
        return "<str>"
    raise PreflightError("unsupported fixture value")


def safe_fixture_document(operation_id: str, raw: JsonValue) -> dict[str, JsonValue]:
    """Return a safe shape-only artifact; it is NEVER verified schema evidence."""
    if operation_id not in {
        "company.get",
        "staff.list",
        "services.list",
        "records.list",
        "record.get",
        "activities.search",
        "clients.search_by_phone",
    }:
        raise PreflightError("unknown operation")
    return {
        "operation_id": operation_id,
        "kind": "shape-only",
        "verified": False,
        "captures_personal_values": False,
        "schema": fixture_shape(raw),
    }


def run_preflight(*, matrix: ApiMatrix | None = None) -> str:
    data = make_preflight(matrix if matrix is not None else load_matrix())
    return json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline T-103 YCLIENTS preflight; never calls API"
    )
    parser.add_argument(
        "--output", type=Path, help="Write static route plan (no secrets, no responses)"
    )
    args = parser.parse_args(argv)
    try:
        content = run_preflight()
    except (PreflightError, ValueError) as error:
        parser.exit(status=2, message=f"Preflight BLOCKED: {error}\n")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
