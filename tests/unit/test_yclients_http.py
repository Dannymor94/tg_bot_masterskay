import asyncio
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from app.integrations.yclients import DecodedPage, YclientsClient
from app.integrations.yclients_policy import AdapterError, ApiMatrix, OperationDenied, load_matrix
from app.settings import load_studio

HOST = "https://yclients.invalid"
URL = HOST + "/api/v1/company/7"


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


def test_headers_timeout_redaction_and_no_library_logs(respx_mock, client_factory, caplog):
    route = respx_mock.get(URL).respond(json={"client_id": 8, "phone": "private-response"})
    caplog.set_level(logging.DEBUG)

    async def scenario():
        async with client_factory() as client:
            result = await client.request("company.get")
        assert isinstance(result, dict) and result["client_id"] == 8
        assert "private-response" not in str(result)

    asyncio.run(scenario())
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer fake-partner, User fake-user"
    assert request.headers["Accept"] == load_matrix().meta.headers_candidates["Accept"]
    assert all(value == 10 for value in request.extensions["timeout"].values())
    assert not any(record.name in {"httpx", "httpcore"} for record in caplog.records)


@pytest.mark.parametrize("status", [429, 500, 501, 502, 503, 504])
def test_three_retries_then_safe_error(status, respx_mock, client_factory):
    route = respx_mock.get(URL).respond(status, text="private-body")
    sleep = AsyncMock()

    async def scenario():
        async with client_factory(sleep=sleep) as client:
            with pytest.raises(AdapterError) as error:
                await client.request("company.get")
            assert error.value.status_code == status
            assert "private-body" not in str(error.value)
            assert "fake-partner" not in repr(error.value)

    asyncio.run(scenario())
    assert route.call_count == 4
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 4]


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_retry_can_recover(failure, respx_mock, client_factory):
    route = respx_mock.get(URL).mock(
        side_effect=[failure("private-url"), httpx.Response(200, json={"id": 1})]
    )
    sleep = AsyncMock()

    async def scenario():
        async with client_factory(sleep=sleep) as client:
            assert await client.request("company.get") == {"id": 1}

    asyncio.run(scenario())
    assert route.call_count == 2
    sleep.assert_awaited_once_with(1)


@pytest.mark.parametrize("status", [302, 400, 401, 403, 404])
def test_terminal_status_does_not_retry_or_redirect(status, respx_mock, client_factory):
    route = respx_mock.get(URL).respond(status, headers={"Location": "https://elsewhere.invalid"})
    sleep = AsyncMock()

    async def scenario():
        async with client_factory(sleep=sleep) as client:
            with pytest.raises(AdapterError) as error:
                await client.request("company.get")
            assert error.value.status_code == status

    asyncio.run(scenario())
    assert route.call_count == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize(
    "retry_after,delay", [("5", 5), ("Thu, 01 Jan 2026 00:00:06 GMT", 6), ("garbage", 1)]
)
def test_retry_after(retry_after, delay, respx_mock, client_factory):
    respx_mock.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": retry_after}),
            httpx.Response(200, json={}),
        ]
    )
    sleep = AsyncMock()

    async def scenario():
        async with client_factory(sleep=sleep) as client:
            await client.request("company.get")

    asyncio.run(scenario())
    sleep.assert_awaited_once_with(delay)


def test_pending_blocked_before_http(respx_mock, client_factory):
    async def scenario():
        async with client_factory(matrix=load_matrix()) as client:
            with pytest.raises(OperationDenied):
                await client.request("company.get")

    asyncio.run(scenario())
    assert not respx_mock.calls


def test_pending_spike_selects_declared_candidate(respx_mock, client_factory):
    route = respx_mock.get(HOST + "/api/v1/staff/7").respond(json={})

    async def scenario():
        async with client_factory(matrix=load_matrix(), allow_pending=True) as client:
            await client.request("staff.list", candidate_index=1)

    asyncio.run(scenario())
    assert route.call_count == 1


def test_post_search_uses_matrix_method(respx_mock, client_factory):
    route = respx_mock.post(HOST + "/api/v1/company/7/clients/search").respond(json={})

    async def scenario():
        async with client_factory() as client:
            await client.request("clients.search_by_phone", body={"phone": "synthetic-query"})

    asyncio.run(scenario())
    assert route.call_count == 1


def test_invalid_json_is_sanitized(respx_mock, client_factory):
    respx_mock.get(URL).respond(text="private-invalid-json")

    async def scenario():
        async with client_factory() as client:
            with pytest.raises(AdapterError) as error:
                await client.request("company.get")
            assert "private-invalid-json" not in str(error.value)

    asyncio.run(scenario())


def test_pagination_uses_declared_page_and_count(respx_mock, client_factory):
    pages = [
        httpx.Response(200, json=[{"client_id": value} for value in range(200)]),
        httpx.Response(200, json=[{"client_id": 200}]),
    ]
    route = respx_mock.get(HOST + "/api/v1/records/7").mock(side_effect=pages)

    async def scenario():
        async with client_factory() as client:
            result = await client.paginate(
                "records.list",
                {"from": "2026-01-01", "to": "2026-01-02"},
                decode_page=lambda value: DecodedPage(value, len(value) == 200),
            )
        assert len(result) == 201

    asyncio.run(scenario())
    assert [call.request.url.params["page"] for call in route.calls] == ["1", "2"]
    assert all(call.request.url.params["count"] == "200" for call in route.calls)
    assert all(call.request.url.params["start_date"] == "2026-01-01" for call in route.calls)


def test_empty_page_finishes_and_decoder_is_explicit(respx_mock, client_factory):
    route = respx_mock.get(HOST + "/api/v1/records/7").respond(json=[])

    async def scenario():
        async with client_factory() as client:
            assert (
                await client.paginate(
                    "records.list",
                    {"from": "x", "to": "y"},
                    decode_page=lambda page: DecodedPage(page, len(page) == 200),
                )
                == []
            )

    asyncio.run(scenario())
    assert route.call_count == 1


def test_undeclared_pagination_is_denied_without_http(respx_mock, client_factory):
    async def scenario():
        async with client_factory() as client:
            with pytest.raises(OperationDenied):
                await client.paginate(
                    "company.get", decode_page=lambda page: DecodedPage(page, len(page) == 200)
                )

    asyncio.run(scenario())
    assert not respx_mock.calls


@pytest.mark.parametrize("decoder", [lambda page: None, lambda page: 1 / 0])
def test_bad_page_decoder_is_sanitized(decoder, respx_mock, client_factory):
    respx_mock.get(HOST + "/api/v1/records/7").respond(json={})

    async def scenario():
        async with client_factory() as client:
            with pytest.raises(AdapterError):
                await client.paginate("records.list", {"from": "x", "to": "y"}, decode_page=decoder)

    asyncio.run(scenario())


def test_unending_pagination_has_a_limit(respx_mock, client_factory):
    route = respx_mock.get(HOST + "/api/v1/records/7").respond(json=[{}] * 200)

    async def scenario():
        async with client_factory() as client:
            client._policy = client._policy.model_copy(update={"max_pages": 2})
            with pytest.raises(AdapterError):
                await client.paginate(
                    "records.list",
                    {"from": "x", "to": "y"},
                    decode_page=lambda page: DecodedPage(page, len(page) == 200),
                )

    asyncio.run(scenario())
    assert route.call_count == 2


@pytest.mark.parametrize(
    "path",
    [
        "GET https://elsewhere.invalid/",
        "GET //elsewhere.invalid/",
        "DELETE /api/v1/company/7",
        "GET /api/../delete",
        "GET /api/{company_id.__class__}",
    ],
)
def test_unsafe_matrix_route_is_denied(path, respx_mock, client_factory):
    matrix = load_matrix().model_dump(mode="json")
    matrix["operations"][0].update(status="verified", path=path)

    async def scenario():
        async with client_factory(matrix=ApiMatrix.model_validate(matrix)) as client:
            with pytest.raises(OperationDenied):
                await client.request("company.get")

    asyncio.run(scenario())
    assert not respx_mock.calls


@pytest.mark.parametrize("record_id", ["../1", "1/extra", "1?mutate=true", "1%2fextra"])
def test_path_values_cannot_change_endpoint(record_id, respx_mock, client_factory):
    async def scenario():
        async with client_factory() as client:
            with pytest.raises(OperationDenied):
                await client.request("record.get", {"record_id": record_id})

    asyncio.run(scenario())
    assert not respx_mock.calls


def test_query_values_are_encoded(respx_mock, client_factory):
    route = respx_mock.get(HOST + "/api/v1/clients/7").respond(json={})

    async def scenario():
        async with client_factory(matrix=load_matrix(), allow_pending=True) as client:
            await client.request(
                "clients.search_by_phone", {"phone": "synthetic&company_id=9"}, candidate_index=1
            )

    asyncio.run(scenario())
    assert route.calls.last.request.url.params["phone"] == "synthetic&company_id=9"
    assert "company_id" not in route.calls.last.request.url.params


@pytest.mark.parametrize("retry_after", ["61", "inf", "nan"])
def test_excessive_or_nonfinite_retry_after_is_not_retried_early(
    retry_after, respx_mock, client_factory
):
    route = respx_mock.get(URL).respond(429, headers={"Retry-After": retry_after})
    sleep = AsyncMock()

    async def scenario():
        async with client_factory(sleep=sleep) as client:
            with pytest.raises(AdapterError):
                await client.request("company.get")

    asyncio.run(scenario())
    assert route.call_count == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize("operation_id", [operation.id for operation in load_matrix().operations])
def test_all_pending_operations_fail_before_http(operation_id, respx_mock, client_factory):
    async def scenario():
        async with client_factory(matrix=load_matrix()) as client:
            with pytest.raises(OperationDenied):
                await client.request(operation_id)

    asyncio.run(scenario())
    assert not respx_mock.calls


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "write", "status": "verified"},
        {"status": "verified", "path": None},
        {"status": "unknown"},
    ],
)
def test_invalid_permission_never_falls_back_to_candidate(change, respx_mock, client_factory):
    matrix = load_matrix().model_dump(mode="json")
    matrix["operations"][0].update(change)

    async def scenario():
        async with client_factory(
            matrix=ApiMatrix.model_validate(matrix), allow_pending=True
        ) as client:
            with pytest.raises(OperationDenied):
                await client.request("company.get")

    asyncio.run(scenario())
    assert not respx_mock.calls


def test_unavailable_is_reported_without_http(respx_mock, client_factory):
    from app.integrations.yclients_policy import OperationUnavailable

    matrix = load_matrix().model_dump(mode="json")
    matrix["operations"][0]["status"] = "unavailable"

    async def scenario():
        async with client_factory(
            matrix=ApiMatrix.model_validate(matrix), allow_pending=True
        ) as client:
            with pytest.raises(OperationUnavailable) as error:
                await client.request("company.get")
            assert error.value.fallback == matrix["operations"][0]["fallback"]

    asyncio.run(scenario())
    assert not respx_mock.calls


def test_transport_retries_are_bounded_and_do_not_leak_exception(respx_mock, client_factory):
    route = respx_mock.get(URL).mock(side_effect=httpx.ReadTimeout("private-transport-details"))
    sleep = AsyncMock()

    async def scenario():
        async with client_factory(sleep=sleep) as client:
            with pytest.raises(AdapterError) as error:
                await client.request("company.get")
            assert "private-transport-details" not in str(error.value)

    asyncio.run(scenario())
    assert route.call_count == 4
    assert sleep.await_count == 3


def test_decoder_controls_completion_and_items_are_redacted(respx_mock, client_factory):
    route = respx_mock.get(HOST + "/api/v1/records/7").mock(
        side_effect=[
            httpx.Response(200, json=[{"client_id": 1, "phone": "private-page-text"}]),
            httpx.Response(200, json=[]),
        ]
    )

    def decode(value):
        # Synthetic test policy: a short first page explicitly has a next page.
        return DecodedPage(value, bool(value))

    async def scenario():
        async with client_factory() as client:
            result = await client.paginate(
                "records.list", {"from": "x", "to": "y"}, decode_page=decode
            )
        assert len(result) == 1
        assert "private-page-text" not in str(result)

    asyncio.run(scenario())
    assert route.call_count == 2


def test_later_page_failure_does_not_return_partial_success(respx_mock, client_factory):
    route = respx_mock.get(HOST + "/api/v1/records/7").mock(
        side_effect=[
            httpx.Response(200, json=[{"client_id": 1}]),
            *(httpx.Response(503) for _ in range(4)),
        ]
    )

    async def scenario():
        async with client_factory(sleep=AsyncMock()) as client:
            with pytest.raises(AdapterError):
                await client.paginate(
                    "records.list",
                    {"from": "x", "to": "y"},
                    decode_page=lambda value: DecodedPage(value, True),
                )

    asyncio.run(scenario())
    assert route.call_count == 5


def test_verified_cannot_select_an_unverified_candidate(respx_mock, client_factory):
    async def scenario():
        async with client_factory(allow_pending=True) as client:
            with pytest.raises(OperationDenied):
                await client.request("staff.list", candidate_index=1)

    asyncio.run(scenario())
    assert not respx_mock.calls
