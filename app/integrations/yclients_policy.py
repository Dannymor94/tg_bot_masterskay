"""Local operation allowlist and fail-closed sanitization, not an API response schema."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError, model_validator

from app.integrations.yclients_guards import safe_read_route, sanitize_diagnostic
from app.texts import ru

API_MATRIX_PATH = Path(__file__).resolve().parents[2] / "docs" / "API_MATRIX.yaml"


class AdapterError(Exception):
    def __init__(self, *, status_code: int | None = None):
        super().__init__(ru.YCLIENTS_ERROR)
        self.status_code = status_code


class OperationDenied(AdapterError):
    def __init__(self):
        super().__init__()
        self.args = (ru.YCLIENTS_DENIED,)


class OperationUnavailable(AdapterError):
    def __init__(self, fallback: str | None):
        super().__init__()
        self.args = (ru.YCLIENTS_UNAVAILABLE,)
        self.fallback = fallback


class Operation(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    kind: str
    status: str
    candidates: tuple[str, ...] = ()
    path: str | None = None
    fallback: str | None = None


class MatrixMeta(BaseModel):
    model_config = ConfigDict(frozen=True)
    headers_candidates: dict[str, str] = {}
    page_size: int | None = None


class ApiMatrix(BaseModel):
    model_config = ConfigDict(frozen=True)
    meta: MatrixMeta
    operations: tuple[Operation, ...]

    @model_validator(mode="after")
    def unique_ids(self) -> "ApiMatrix":
        if len({operation.id for operation in self.operations}) != len(self.operations):
            raise ValueError
        return self

    def resolve(self, operation_id: str, *, allow_pending: bool = False) -> Operation:
        operation = next((op for op in self.operations if op.id == operation_id), None)
        if operation is None or operation.kind != "read":
            raise OperationDenied
        if operation.status == "unavailable":
            raise OperationUnavailable(operation.fallback)
        if operation.status != "verified" and not (allow_pending and operation.status == "pending"):
            raise OperationDenied
        if operation.status == "verified":
            if operation.path is None or not safe_read_route(operation.id, operation.path):
                raise OperationDenied
        elif not operation.candidates or any(
            not safe_read_route(operation.id, template) for template in operation.candidates
        ):
            # Even the explicit owner-only spike cannot mint unsafe API routes.
            raise OperationDenied
        return operation


def load_matrix(path: Path = API_MATRIX_PATH) -> ApiMatrix:
    try:
        return ApiMatrix.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, ValidationError, yaml.YAMLError):
        raise AdapterError from None


def redact(value: JsonValue, *, _key: str = "") -> JsonValue:
    """Fail-closed diagnostics only: never use this to normalize business data.

    Unknown JSON keys are discarded because keys themselves may contain PII.
    The safe key labels are NOT evidence of a verified YCLIENTS schema.
    """
    return sanitize_diagnostic(value, _key=_key)
