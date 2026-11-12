import asyncio
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from app.integrations.yclients import YclientsClient
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
