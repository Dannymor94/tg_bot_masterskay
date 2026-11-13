"""T-103 preparation tests: deterministic, synthetic, zero network and credentials."""

import json

import pytest

from app.integrations.yclients_policy import ApiMatrix, load_matrix
from scripts.spike_preflight import (
    PreflightError,
    fixture_shape,
    make_preflight,
    run_preflight,
    safe_fixture_document,
)


def test_manifest_is_stable_and_explicitly_not_verified():
    text = run_preflight()
    assert text == run_preflight()
    data = json.loads(text)
    assert data["mode"] == "offline-preflight"
    assert data["secret_access"] is False
    assert data["network_access"] is False
    assert data["confirms_external_api"] is False
    assert len(data["operations"]) == 7
    for op in data["operations"]:
        assert op["current_status"] == "pending"
        assert op["requires_human_evidence"] is True
        assert op["auto_promoted"] is False
        assert all(c["read_only_allowlisted"] for c in op["candidates"])
        assert all(c["api_verified"] is False for c in op["candidates"])


def test_placeholder_discovery_is_sorted_and_no_real_values():
    data = make_preflight(load_matrix())
    ops = {op["operation_id"]: op for op in data["operations"]}
    c = ops["records.list"]["candidates"][0]
    assert c["required_parameters"] == ["company_id", "count", "from", "page", "to"]
    assert "2026" not in json.dumps(data)
    assert ops["clients.search_by_phone"]["candidates"][0]["method"] == "POST"


def test_rejects_verified_forged_write_path():
    data = load_matrix().model_dump(mode="json")
    data["operations"][0]["status"] = "verified"
    data["operations"][0]["path"] = "POST /api/v1/company/{company_id}/delete"
    with pytest.raises(PreflightError):
        make_preflight(ApiMatrix.model_validate(data))


def test_rejects_candidate_tampering_before_http():
    data = load_matrix().model_dump(mode="json")
    data["operations"][0]["candidates"].append("GET /api/v1/company/{company_id}?write=true")
    with pytest.raises(PreflightError):
        make_preflight(ApiMatrix.model_validate(data))


def test_verified_code_allowed_route_is_not_claimed_as_new_verification():
    data = load_matrix().model_dump(mode="json")
    data["operations"][0].update(
        status="verified", path=data["operations"][0]["candidates"][0], candidates=[]
    )
    manifest = make_preflight(ApiMatrix.model_validate(data))
    first = next(op for op in manifest["operations"] if op["operation_id"] == "company.get")
    assert first["auto_promoted"] is False
    assert first["current_status"] == "verified"
    assert first["candidates"] == []


def test_shape_sanitizes_pii_in_values_and_dynamic_keys():
    synthetic_phone = "+7-999-123-45-67"
    synthetic_name = "PRIVATE-PERSON"
    raw = {
        synthetic_phone: {"name": synthetic_name},
        "record_id": 1234567,
        "starts_at": "2026-11-13T12:00:00+03:00",
        "count": 7,
        "nested": [{"phone": synthetic_phone, "name": synthetic_name, "token": "secret"}],
    }
    artifact = safe_fixture_document("records.list", raw)
    encoded = json.dumps(artifact, ensure_ascii=False)
    for value in (synthetic_phone, synthetic_name, "1234567", "2026-11-13", "secret"):
        assert value not in encoded
    assert artifact["verified"] is False
    assert artifact["schema"]["record_id"] == "<int>"
    assert artifact["schema"]["starts_at"] == "<str>"
    assert raw[synthetic_phone]["name"] == synthetic_name


def test_bool_integer_float_null_shapes_are_distinct():
    shaped = fixture_shape({"success": True, "count": 42, "price": 4.5, "missing": None})
    assert shaped == {
        "count": "<int>",
        "missing": "<null>",
        "price": "<float>",
        "success": "<bool>",
    }


def test_unknown_deep_and_non_json_types_fail_closed():
    with pytest.raises(PreflightError):
        fixture_shape([[[[[[[[[[[[1]]]]]]]]]]]])
    with pytest.raises(PreflightError):
        fixture_shape({"record_id": object()})  # type: ignore[dict-item]
    with pytest.raises(PreflightError):
        fixture_shape({7: "pii"})  # type: ignore[dict-item]


def test_list_samples_are_bounded_and_scalar_content_hidden():
    raw = [{"record_id": i, "phone": f"secret-{i}"} for i in range(30)]
    sanitized = fixture_shape(raw)
    assert len(sanitized) == 3
    assert all(x == {"phone": "<str>", "record_id": "<int>"} for x in sanitized)
    assert "secret" not in repr(sanitized)


def test_invalid_operation_is_rejected():
    with pytest.raises(PreflightError):
        safe_fixture_document("owner.private", {"id": 1})
