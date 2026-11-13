from pathlib import Path

import pytest

from app.integrations.yclients_policy import (
    API_MATRIX_PATH,
    AdapterError,
    ApiMatrix,
    OperationDenied,
    OperationUnavailable,
    load_matrix,
    redact,
)


def test_every_real_pending_operation_is_denied():
    matrix = load_matrix()
    for operation in matrix.operations:
        assert operation.status == "pending"
        with pytest.raises(OperationDenied):
            matrix.resolve(operation.id)
        assert matrix.resolve(operation.id, allow_pending=True) == operation


@pytest.mark.parametrize(
    "kind,status", [("write", "verified"), ("write", "pending"), ("read", "unknown")]
)
@pytest.mark.parametrize("allow_pending", [False, True])
def test_fail_closed_policy(kind, status, allow_pending):
    matrix = ApiMatrix.model_validate(
        {
            "meta": {},
            "operations": [
                {
                    "id": "company.get",
                    "kind": kind,
                    "status": status,
                    "path": "GET /api/v1/company/{company_id}",
                }
            ],
        }
    )
    with pytest.raises(OperationDenied):
        matrix.resolve("company.get", allow_pending=allow_pending)


def test_unavailable_exposes_documented_fallback():
    matrix = load_matrix().model_dump(mode="json")
    matrix["operations"][0]["status"] = "unavailable"
    matrix = ApiMatrix.model_validate(matrix)
    with pytest.raises(OperationUnavailable) as error:
        matrix.resolve("company.get", allow_pending=True)
    assert error.value.fallback == matrix.operations[0].fallback


def test_unknown_id_is_not_echoed():
    with pytest.raises(OperationDenied) as error:
        load_matrix().resolve("private-input")
    assert "private-input" not in str(error.value)


@pytest.mark.parametrize("content", ["[", "operations: []", "meta: {}\noperations: null"])
def test_bad_matrix_is_sanitized(tmp_path, content):
    path = tmp_path / "matrix.yaml"
    path.write_text(content)
    with pytest.raises(AdapterError):
        load_matrix(path)


def test_duplicate_operation_is_rejected(tmp_path):
    matrix = load_matrix().model_dump(mode="json")
    matrix["operations"].append(matrix["operations"][0])
    import yaml

    path = tmp_path / "matrix.yaml"
    path.write_text(yaml.safe_dump(matrix))
    with pytest.raises(AdapterError):
        load_matrix(path)


def test_redaction_is_recursive_and_does_not_mutate():
    source = {
        "client_id": 7,
        "phone": 12345678901,
        "name": "private-name",
        "nested": [{"token": "private-token", "extra": "private-text", "count": 123}],
        "success": True,
        "missing": None,
    }
    sanitized = redact(source)
    assert isinstance(sanitized, dict)
    rendered = str(sanitized)
    for private in ("12345678901", "private-name", "private-token", "private-text", "123"):
        assert private not in rendered
    assert sanitized["client_id"] == 7
    assert sanitized["success"] is True
    assert sanitized["missing"] is None
    assert source["phone"] == 12345678901
    assert redact(sanitized) == sanitized


def test_matrix_path_is_inside_repository():
    assert API_MATRIX_PATH == Path(__file__).resolve().parents[2] / "docs" / "API_MATRIX.yaml"
