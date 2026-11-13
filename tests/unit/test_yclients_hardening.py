"""T-102 review regression probes; synthetic responses, no live HTTP endpoints."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

from app.integrations.yclients import DecodedPage, YclientsClient
from app.integrations.yclients_guards import SAFE_READ_ROUTES, safe_read_route
from app.integrations.yclients_policy import AdapterError, ApiMatrix, OperationDenied, load_matrix, redact
from app.settings import load_studio

HOST = "https://yclients.invalid"
RECORDS = HOST + "/api/v1/records/7"


class ScheduleProbe(BaseModel):
    """Synthetic internal DTO, NOT a claimed YCLIENTS wire model."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    record_id: int
    starts_at: datetime
    count: int


@pytest.fixture
def client_factory():
    matrix = load_matrix().model_dump(mode="json")
    for operation in matrix["operations"]:
        operation["status"] = "verified"
        operation["path"] = operation["candidates"][0]

    def factory(**kwargs):
        return YclientsClient(
            base_url=HOST,
            partner_token=SecretStr("fake-partner"),
            user_token=SecretStr("fake-user"),
            company_id=7,
            policy=load_studio().yclients_http,
            matrix=kwargs.pop("matrix", ApiMatrix.model_validate(matrix)),
            now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
            **kwargs,
        )

    return factory


def test_code_allowlist_covers_declared_candidates_without_implicitly_verifying_them():
    matrix = load_matrix()
    assert {op.id for op in matrix.operations} == set(SAFE_READ_ROUTES)
    for operation in matrix.operations:
        assert operation.status == "pending"
        assert set(operation.candidates) == set(SAFE_READ_ROUTES[operation.id])
    assert not safe_read_route(
        "company.get", "POST /api/v1/records/{company_id}/delete"
    )


@pytest.mark.parametrize(
    "forged_route",
    [
        "POST /api/v1/records/{company_id}/delete",
        "GET /api/v1/company/{company_id}?write=true",
        "GET /api/v1/staff/{company_id}",
    ],
)
def test_forged_verified_route_denied_before_network(
    forged_route, respx_mock, client_factory
):
    matrix = load_matrix().model_dump(mode="json")
    matrix["operations"][0].update(status="verified", path=forged_route)

    async def scenario():
        async with client_factory(matrix=ApiMatrix.model_validate(matrix)) as client:
            with pytest.raises(OperationDenied):
                await client.request("company.get")

    asyncio.run(scenario())
    assert not respx_mock.calls


def test_spike_cannot_run_new_unapproved_candidate(respx_mock, client_factory):
    matrix = load_matrix().model_dump(mode="json")
    matrix["operations"][0]["candidates"].append(
        "POST /api/v1/records/{company_id}/delete"
    )

    async def scenario():
        async with client_factory(
            matrix=ApiMatrix.model_validate(matrix), allow_pending=True
        ) as client:
            with pytest.raises(OperationDenied):
                await client.request("company.get", candidate_index=0)

    asyncio.run(scenario())
    assert not respx_mock.calls


def test_verified_route_remains_allowed_after_spike_removes_candidates(
    respx_mock, client_factory
):
    route = respx_mock.get(HOST + "/api/v1/company/7").respond(json={"id": 7})
    matrix = load_matrix().model_dump(mode="json")
    matrix["operations"][0].update(
        status="verified",
        path="GET /api/v1/company/{company_id}",
        candidates=[],
    )

    async def scenario():
        async with client_factory(matrix=ApiMatrix.model_validate(matrix)) as client:
            assert await client.request("company.get") == {"id": 7}

    asyncio.run(scenario())
    assert route.call_count == 1


def test_redact_discards_pii_in_dynamic_json_keys_and_does_not_mutate():
    data = {
        "79991234567": {"name": "private"},
        "other@example.org": "private",
        "nested": [{"token": "private-secret", "count": 12}],
        "record_id": 5,
        "starts_at": "2026-11-13T10:00:00+03:00",
    }
    result = redact(data)
    assert isinstance(result, dict)
    assert "79991234567" not in repr(result)
    assert "other@example.org" not in repr(result)
    assert "private-secret" not in repr(result)
    assert result["record_id"] == 5
    assert result["starts_at"] == "[REDACTED]"
    assert data["79991234567"]["name"] == "private"
    assert redact(result) == result


def test_trusted_dto_boundary_keeps_dates_and_counts_in_memory(
    respx_mock, client_factory
):
    payload = {
        "record_id": 1,
        "starts_at": "2026-11-13T10:00:00+03:00",
        "count": 12,
        "phone": "private-PII",
    }
    route = respx_mock.get(HOST + "/api/v1/company/7").respond(json=payload)

    def normalize(raw):
        # T-103 must supply verified field mapping before production use.
        return ScheduleProbe.model_validate(raw)

    async def scenario():
        async with client_factory() as client:
            diagnostic = await client.request("company.get")
            assert isinstance(diagnostic, dict)
            normalized = await client.request_normalized(
                "company.get", normalize=normalize
            )
            assert diagnostic["record_id"] == 1
            assert diagnostic["starts_at"] == "[REDACTED]"
            assert diagnostic["count"] == "[REDACTED]"
            assert "private-PII" not in repr(diagnostic)
            assert normalized.record_id == 1
            assert normalized.count == 12
            assert normalized.starts_at.utcoffset() is not None

    asyncio.run(scenario())
    assert route.call_count == 2


def test_normalizer_errors_and_untyped_result_do_not_leak_private_json(
    respx_mock, client_factory
):
    route = respx_mock.get(HOST + "/api/v1/company/7").respond(
        json={"phone": "private-PII"}
    )

    async def scenario():
        async with client_factory() as client:
            for normalize in (
                lambda raw: raw,
                lambda raw: (_ for _ in ()).throw(RuntimeError("private-PII")),
            ):
                with pytest.raises(AdapterError) as error:
                    await client.request_normalized(
                        "company.get", normalize=normalize  # type: ignore[arg-type]
                    )
                assert "private-PII" not in str(error.value)

    asyncio.run(scenario())
    assert route.call_count == 2


def test_normalized_pagination_emits_validated_dtos(respx_mock, client_factory):
    side_effect = [
        httpx.Response(200, json={
            "items": [{
                "record_id": 1,
                "starts_at": "2026-11-13T10:00:00+03:00",
                "count": 9,
                "phone": "private-one",
            }],
            "has_more": True,
        }),
        httpx.Response(200, json={
            "items": [{
                "record_id": 2,
                "starts_at": "2026-11-13T11:00:00+03:00",
                "count": 7,
                "phone": "private-two",
            }],
            "has_more": False,
        }),
    ]
    route = respx_mock.get(RECORDS).mock(side_effect=side_effect)

    def decode(raw):
        return DecodedPage(raw["items"], raw["has_more"])

    async def scenario():
        async with client_factory() as client:
            records = await client.paginate_normalized(
                "records.list",
                {"from": "2026-11-13", "to": "2026-11-14"},
                decode_page=decode,
                normalize_item=ScheduleProbe.model_validate,
            )
            assert [record.record_id for record in records] == [1, 2]
            assert [record.count for record in records] == [9, 7]

    asyncio.run(scenario())
    assert route.call_count == 2


def test_repeated_page_fails_even_if_terminal_flag_changes(respx_mock, client_factory):
    repeated = [{"record_id": 1, "phone": "private-phone"}]
    route = respx_mock.get(RECORDS).mock(side_effect=[
        httpx.Response(200, json={"items": repeated, "has_more": True}),
        httpx.Response(200, json={"items": repeated, "has_more": False}),
    ])

    async def scenario():
        async with client_factory() as client:
            with pytest.raises(AdapterError) as error:
                await client.paginate(
                    "records.list",
                    {"from": "x", "to": "y"},
                    decode_page=lambda raw: DecodedPage(
                        raw["items"], raw["has_more"]
                    ),
                )
            assert "private-phone" not in str(error.value)

    asyncio.run(scenario())
    assert route.call_count == 2


def test_incomplete_empty_intermediate_page_fails_closed(respx_mock, client_factory):
    route = respx_mock.get(RECORDS).respond(json={"items": [], "has_more": True})

    async def scenario():
        async with client_factory() as client:
            with pytest.raises(AdapterError):
                await client.paginate(
                    "records.list",
                    {"from": "x", "to": "y"},
                    decode_page=lambda raw: DecodedPage(
                        raw["items"], raw["has_more"]
                    ),
                )

    asyncio.run(scenario())
    assert route.call_count == 1
