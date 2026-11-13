"""Reviewed local allowlist and diagnostic safety guards for YCLIENTS T-102.

Matrix evidence is not sufficient to authorize a new route. A new route
requires a code change, tests and independent review before it can execute.
"""

import hashlib
import json
from collections.abc import Mapping

from pydantic import JsonValue

SAFE_READ_ROUTES: Mapping[str, frozenset[str]] = {
    "company.get": frozenset({"GET /api/v1/company/{company_id}"}),
    "staff.list": frozenset(
        {
            "GET /api/v1/company/{company_id}/staff/",
            "GET /api/v1/staff/{company_id}",
        }
    ),
    "services.list": frozenset(
        {
            "GET /api/v1/company/{company_id}/services/",
            "GET /api/v1/services/{company_id}",
        }
    ),
    "records.list": frozenset(
        {
            "GET /api/v1/records/{company_id}?start_date={from}&end_date={to}&page={page}&count={count}"
        }
    ),
    "record.get": frozenset({"GET /api/v1/record/{company_id}/{record_id}"}),
    "activities.search": frozenset(
        {"GET /api/v1/activity/{company_id}/search?from={from}&till={to}"}
    ),
    "clients.search_by_phone": frozenset(
        {
            "POST /api/v1/company/{company_id}/clients/search",
            "GET /api/v1/clients/{company_id}?phone={phone}",
        }
    ),
}

# Safe diagnostic KEY labels, not a confirmed external API schema.
# All unknown keys are dropped: PII may occur in dictionary keys as well.
SAFE_DIAGNOSTIC_KEYS = frozenset(
    {
        "id",
        "record_id",
        "client_id",
        "staff_id",
        "service_id",
        "activity_id",
        "resource_id",
        "company_id",
        "hall_code",
        "owner_id",
        "telegram_user_id",
        "name",
        "phone",
        "token",
        "extra",
        "nested",
        "missing",
        "success",
        "count",
        "starts_at",
        "ends_at",
        "date",
        "time",
        "price",
        "prices",
        "total",
        "page",
        "pages",
        "has_more",
        "data",
        "items",
        "results",
        "payload",
        "status",
        "deleted",
        "updated_at",
        "created_at",
        "value",
        "type",
    }
)


def safe_read_route(operation_id: str, template: str) -> bool:
    """Only code-reviewed METHOD+path templates may be requested."""
    return template in SAFE_READ_ROUTES.get(operation_id, frozenset())


def sanitize_diagnostic(value: JsonValue, *, _key: str = "") -> JsonValue:
    """Preserve safe structure, suppress unknown keys and sensitive values."""
    if isinstance(value, dict):
        return {
            key: sanitize_diagnostic(item, _key=key)
            for key, item in value.items()
            if key in SAFE_DIAGNOSTIC_KEYS
        }
    if isinstance(value, list):
        return [sanitize_diagnostic(item) for item in value]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and (_key == "id" or _key.endswith("_id")):
        return value
    return "[REDACTED]"


def fingerprint_page(items: list[JsonValue]) -> bytes:
    """Non-logged digest of raw in-memory items; detects repeat pages."""
    encoded = json.dumps(items, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).digest()
