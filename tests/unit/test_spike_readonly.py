"""T-103b spike tests: synthetic, zero network (respx), zero real secrets.

They exercise the owner gate (fail closed before secrets/network), the probe
loop, status/error categorization, shape-only evidence, phone handling and the
guarantee that no secrets or PII reach reports, files or the proposal.
"""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from pydantic import SecretStr

from app.integrations.yclients import YclientsClient
from app.integrations.yclients_policy import (
    API_MATRIX_PATH,
    AdapterError,
    ApiMatrix,
    OperationDenied,
    load_matrix,
)
from app.settings import load_studio
from scripts import spike_readonly
from scripts.spike_readonly import _categorize, main, run_spike

HOST = "https://yclients.invalid"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
PII = ("PRIVATE-PERSON", "+7-999-123-45-67", "secret-token", "fake-partner", "fake-user")


@pytest.fixture
def client_factory():
    def factory(**kwargs):
        return YclientsClient(
            base_url=HOST,
            partner_token=SecretStr("fake-partner"),
            user_token=SecretStr("fake-user"),
            company_id=7,
            policy=load_studio().yclients_http,
            matrix=load_matrix(),
            allow_pending=True,
            now=lambda: NOW,
            **kwargs,
        )

    return factory


def _run(client, tmp_path, **kwargs):
    async def scenario():
        async with client as c:
            return await run_spike(
                client=c, matrix=load_matrix(), output_dir=tmp_path, now=NOW, **kwargs
            )

    return asyncio.run(scenario())


def _ok(route_mock):
    # A realistic-looking but fully synthetic body with PII in values and keys.
    route_mock.respond(
        json={
            "success": True,
            "data": [
                {
                    "id": 1,
                    "staff_id": 2,
                    "name": "PRIVATE-PERSON",
                    "phone": "+7-999-123-45-67",
                    "token": "secret-token",
                    "+7-999-123-45-67": "dynamic-pii-key",
                }
            ],
        }
    )


def test_all_good_probes_write_shape_only_evidence_without_pii(
    respx_mock, client_factory, tmp_path
):
    for prefix in (
        "/api/v1/company/7",
        "/api/v1/records/7",
        "/api/v1/activity/7",
        "/api/v1/staff/7",  # staff.list candidate 0 after T-103 removed the 403 candidate
    ):
        _ok(respx_mock.get(url__regex=rf"{HOST}{prefix}"))

    report = _run(client_factory(), tmp_path)

    assert report["mode"] == "live-owner-spike"
    assert report["verified_promoted"] is False
    assert report["matrix_modified"] is False
    assert report["needs_independent_approval"] is True
    assert report["company_scope_bound"] is True

    ops = {r["operation_id"]: r for r in report["operations"]}
    assert ops["company.get"]["probed_ok"] is True
    assert ops["company.get"]["proposed_candidate_index"] == 0
    # record.get is never probed (needs a real id); phone search needs a phone.
    assert "not_probed" in (ops["record.get"]["note"] or "")
    assert "unavailable" in (ops["clients.search_by_phone"]["note"] or "")

    blob = json.dumps(report)
    files = list(tmp_path.glob("*.json"))
    assert {f.stem for f in files} >= {"company.get", "records.list", "activities.search"}
    for f in files:
        text = f.read_text()
        assert '"verified": false' in text
        for secret in PII:
            assert secret not in text
    for secret in PII:
        assert secret not in blob
    # Every live call used a read method only; nothing was written.
    assert all(call.request.method in {"GET", "POST"} for call in respx_mock.calls)


def test_candidate_fallback_records_each_attempt(respx_mock, client_factory, tmp_path):
    # services.list: candidate 0 (/company/{id}/services/) fails 404;
    # spike falls through to candidate 1 (/services/{id}) which succeeds.
    # This covers the genuine two-candidate fallback path required by Issue #64.
    respx_mock.get(url__regex=rf"{HOST}/api/v1/company/7/services/").respond(404)
    _ok(respx_mock.get(url__regex=rf"{HOST}/api/v1/services/7"))
    respx_mock.route(host="yclients.invalid").respond(500)  # everything else fails

    report = _run(client_factory(sleep=AsyncMock()), tmp_path)
    services = next(r for r in report["operations"] if r["operation_id"] == "services.list")
    assert services["probed_ok"] is True
    assert services["proposed_candidate_index"] == 1
    assert [a["category"] for a in services["attempts"]] == ["http_404", "ok"]


@pytest.mark.parametrize(
    "status,expected",
    [(401, "http_401"), (403, "http_403"), (404, "http_404")],
)
def test_client_error_categories(status, expected, respx_mock, client_factory, tmp_path):
    respx_mock.route(host="yclients.invalid").respond(status)
    report = _run(client_factory(sleep=AsyncMock()), tmp_path)
    company = next(r for r in report["operations"] if r["operation_id"] == "company.get")
    assert company["probed_ok"] is False
    assert company["attempts"][0]["category"] == expected


def test_server_error_and_network_and_malformed(respx_mock, client_factory, tmp_path):
    respx_mock.get(url__regex=rf"{HOST}/api/v1/company/7").respond(503)
    respx_mock.get(url__regex=rf"{HOST}/api/v1/records/7").mock(
        side_effect=httpx.ConnectTimeout("x")
    )
    respx_mock.get(url__regex=rf"{HOST}/api/v1/activity/7").respond(200, text="not-json")
    respx_mock.route(host="yclients.invalid").respond(200, json={})

    report = _run(client_factory(sleep=AsyncMock()), tmp_path)
    cat = {
        r["operation_id"]: r["attempts"][0]["category"]
        for r in report["operations"]
        if r["attempts"]
    }
    assert cat["company.get"] == "http_503"  # retried then exhausted
    assert cat["records.list"] == "no_response_or_malformed"  # network timeout
    assert cat["activities.search"] == "http_200"  # 200 but body is not JSON


def test_phone_search_probes_privately_without_leaking(respx_mock, client_factory, tmp_path):
    respx_mock.post(url__regex=rf"{HOST}/api/v1/company/7/clients/search").respond(
        json={"data": [{"id": 1, "phone": "+7-999-123-45-67", "name": "PRIVATE-PERSON"}]}
    )
    respx_mock.route(host="yclients.invalid").respond(200, json={})

    report = _run(client_factory(), tmp_path, phone="+7-999-123-45-67")
    phone_op = next(
        r for r in report["operations"] if r["operation_id"] == "clients.search_by_phone"
    )
    assert phone_op["probed_ok"] is True
    assert phone_op["attempts"][0]["method"] == "POST"
    # The phone body was sent but never appears in report or evidence.
    assert "+7-999-123-45-67" not in json.dumps(report)
    for f in tmp_path.glob("*.json"):
        assert "+7-999-123-45-67" not in f.read_text()
    post = next(c.request for c in respx_mock.calls if c.request.method == "POST")
    assert "+7-999-123-45-67" in post.content.decode()  # proves it reached the wire, not our output


@pytest.mark.parametrize("page_size", [None, 0, -1, False, True])
def test_invalid_page_size_blocks_before_http_or_evidence(
    page_size, respx_mock, client_factory, tmp_path
):
    base = load_matrix()
    matrix = base.model_copy(update={"meta": base.meta.model_copy(update={"page_size": page_size})})
    respx_mock.route(host="yclients.invalid").respond(200, json={"data": []})

    async def scenario():
        async with client_factory(sleep=AsyncMock()) as client:
            return await run_spike(client=client, matrix=matrix, output_dir=tmp_path, now=NOW)

    with pytest.raises(AdapterError) as error:
        asyncio.run(scenario())

    assert "secret-token" not in str(error.value)
    assert not respx_mock.calls
    assert not list(tmp_path.iterdir())


def test_valid_page_size_reaches_records_list_unchanged(respx_mock, client_factory, tmp_path):
    base = load_matrix()
    matrix = base.model_copy(update={"meta": base.meta.model_copy(update={"page_size": 23})})
    respx_mock.route(host="yclients.invalid").respond(403)

    async def scenario():
        async with client_factory(sleep=AsyncMock()) as client:
            return await run_spike(client=client, matrix=matrix, output_dir=tmp_path, now=NOW)

    report = asyncio.run(scenario())
    record_requests = [
        call.request for call in respx_mock.calls if "/api/v1/records/7" in str(call.request.url)
    ]

    assert len(record_requests) == 1
    assert record_requests[0].url.params["count"] == "23"
    assert record_requests[0].url.params["page"] == "1"
    records = next(r for r in report["operations"] if r["operation_id"] == "records.list")
    assert records["attempts"][0]["category"] == "http_403"
    assert records["probed_ok"] is False
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("configured_size", "expected_count"),
    [
        pytest.param(True, None, id="yaml-true"),
        pytest.param(False, None, id="yaml-false"),
        pytest.param(None, None, id="yaml-null"),
        pytest.param("missing-key", None, id="yaml-missing"),
        pytest.param(0, None, id="yaml-zero"),
        pytest.param(-1, None, id="yaml-negative"),
        pytest.param(-99, None, id="yaml-negative-large"),
        pytest.param("1", None, id="yaml-numeric-string"),
        pytest.param(1.0, None, id="yaml-float"),
        pytest.param(1, 1, id="yaml-one"),
        pytest.param(23, 23, id="yaml-twenty-three"),
        pytest.param(200, 200, id="yaml-default"),
    ],
)
def test_page_size_via_real_yaml_loader_fails_closed(
    configured_size, expected_count, respx_mock, client_factory, tmp_path, capsys
):
    """The file loader must not coerce YAML booleans into valid page sizes."""
    raw = yaml.safe_load(API_MATRIX_PATH.read_text(encoding="utf-8"))
    if configured_size == "missing-key":
        raw["meta"].pop("page_size")
    else:
        raw["meta"]["page_size"] = configured_size
    matrix_path = tmp_path / "matrix.yaml"
    matrix_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    evidence_dir = tmp_path / "evidence"
    respx_mock.route(host="yclients.invalid").respond(403)

    try:
        matrix = load_matrix(matrix_path)
    except AdapterError as error:
        assert expected_count is None
        assert not any(secret in str(error) for secret in PII)
    else:

        async def scenario():
            async with client_factory(sleep=AsyncMock()) as client:
                return await run_spike(
                    client=client, matrix=matrix, output_dir=evidence_dir, now=NOW
                )

        if expected_count is None:
            with pytest.raises(AdapterError) as error:
                asyncio.run(scenario())
            assert not any(secret in str(error.value) for secret in PII)
        else:
            report = asyncio.run(scenario())
            assert report["requests_made"] > 0
            record_calls = [
                call.request
                for call in respx_mock.calls
                if "/api/v1/records/7" in str(call.request.url)
            ]
            assert len(record_calls) == 1
            assert record_calls[0].url.params["count"] == str(expected_count)
            assert record_calls[0].url.params["page"] == "1"

    if expected_count is None:
        assert not respx_mock.calls
    assert not evidence_dir.exists()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""


def test_request_budget_fails_closed(respx_mock, client_factory, tmp_path):
    respx_mock.route(host="yclients.invalid").respond(200, json={})
    with pytest.raises(AdapterError):
        _run(client_factory(), tmp_path, budget=0)


def test_gate_fails_closed_before_reading_secrets(monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise AssertionError("load_settings must not run before authorization")

    monkeypatch.setattr(spike_readonly, "load_settings", boom)
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2
    assert "BLOCKED" in capsys.readouterr().err


def test_authorized_non_tty_runs_without_prompt(monkeypatch):
    sentinel = {"mode": "live-owner-spike"}

    async def fake_run(settings, *, phone, output_dir):
        assert phone is None
        return sentinel

    monkeypatch.setattr(spike_readonly, "load_settings", lambda: object())
    monkeypatch.setattr(spike_readonly, "_run_live", fake_run)
    monkeypatch.setattr(spike_readonly.sys.stdin, "isatty", lambda: False)
    assert main(["--authorize-live-read"]) == 0


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {
                "success": False,
                "data": {"code": 15, "message": "PRIVATE-PERSON secret-token"},
                "meta": {"phone": "+7-999-123-45-67"},
            },
            "http_403_code_15",
        ),
        ({"success": False, "data": {"code": 16, "message": "secret-token"}}, "http_403"),
        ({"success": False, "data": {"code": "15"}}, "http_403"),
        ({"success": False, "data": {"code": True}}, "http_403"),
        ({"success": True, "data": {"code": 15}}, "http_403"),
        ({"data": {"code": 15}}, "http_403"),
        ({"success": False, "error": {"code": 15}}, "http_403"),
    ],
)
def test_403_diagnostic_only_allows_exact_numeric_code_15(
    body, expected, respx_mock, client_factory, tmp_path
):
    respx_mock.get(url__regex=rf"{HOST}/api/v1/records/7").respond(
        403, json=body, headers={"X-Debug": "secret-token"}
    )
    respx_mock.route(host="yclients.invalid").respond(403)

    report = _run(client_factory(sleep=AsyncMock()), tmp_path)
    records = next(r for r in report["operations"] if r["operation_id"] == "records.list")

    assert records["attempts"][0]["category"] == expected
    assert records["probed_ok"] is False
    assert sum("/api/v1/records/7" in str(call.request.url) for call in respx_mock.calls) == 1
    assert not list(tmp_path.glob("*.json"))
    output = json.dumps(report)
    for private_value in PII:
        assert private_value not in output


@pytest.mark.parametrize(
    "raw_error",
    [
        "{not-json",
        json.dumps(
            {
                "success": False,
                "data": {"code": 15, "message": "secret-token" * 500},
            }
        ),
    ],
)
def test_403_malformed_or_oversized_body_is_generic(
    raw_error, respx_mock, client_factory, tmp_path
):
    respx_mock.get(url__regex=rf"{HOST}/api/v1/records/7").respond(
        403, text=raw_error, headers={"X-Debug": "PRIVATE-PERSON"}
    )
    respx_mock.route(host="yclients.invalid").respond(403)

    report = _run(client_factory(sleep=AsyncMock()), tmp_path)
    records = next(r for r in report["operations"] if r["operation_id"] == "records.list")

    assert records["attempts"][0]["category"] == "http_403"
    assert "secret-token" not in json.dumps(report)
    assert "PRIVATE-PERSON" not in json.dumps(report)


def test_401_with_403_error_envelope_stays_401(respx_mock, client_factory, tmp_path):
    respx_mock.get(url__regex=rf"{HOST}/api/v1/records/7").respond(
        401, json={"success": False, "data": {"code": 15, "message": "secret-token"}}
    )
    respx_mock.route(host="yclients.invalid").respond(403)

    report = _run(client_factory(sleep=AsyncMock()), tmp_path)
    records = next(r for r in report["operations"] if r["operation_id"] == "records.list")
    assert records["attempts"][0]["category"] == "http_401"


# T-103 synthetic regression tests (T1–T3 from docs/reviews/T-103c-DIAGNOSIS.md §B.1)


def test_t1_records_list_403_no_retry_one_http_call(respx_mock, client_factory, tmp_path):
    """T1: records.list 403 → category "http_403", exactly 1 HTTP call (no retry on 4xx).

    Also verifies the adapter sends the full dual-token Authorization header
    configured in API_MATRIX (Bearer {partner} + User {user}).
    The header format is what the adapter sends; whether YCLIENTS accepts it
    as valid auth is not asserted here — that requires a live probe.
    """
    respx_mock.get(url__regex=rf"{HOST}/api/v1/records/7").respond(403)
    respx_mock.route(host="yclients.invalid").respond(200, json={})

    report = _run(client_factory(sleep=AsyncMock()), tmp_path)

    records_op = next(r for r in report["operations"] if r["operation_id"] == "records.list")
    assert records_op["probed_ok"] is False
    assert records_op["attempts"][0]["category"] == "http_403"

    records_calls = [c for c in respx_mock.calls if "/api/v1/records/" in str(c.request.url)]
    assert len(records_calls) == 1  # 403 must not trigger retry

    # Positive: dual-token header is present on the request.
    auth = records_calls[0].request.headers.get("authorization", "")
    assert "Bearer fake-partner" in auth
    assert "User fake-user" in auth

    # Negative: Bearer-only Authorization (no User token) is a different adapter config.
    # Construct a matrix that omits the User token from the header template and build
    # the client directly (factory hardcodes the dual-token matrix).
    base = load_matrix()
    bearer_only_headers = dict(base.meta.headers_candidates)
    bearer_only_headers["Authorization"] = "Bearer {partner_token}"
    bearer_only_meta = base.meta.model_copy(update={"headers_candidates": bearer_only_headers})
    bearer_only_matrix = base.model_copy(update={"meta": bearer_only_meta})

    respx_mock.reset()
    respx_mock.get(url__regex=rf"{HOST}/api/v1/records/7").respond(403)
    respx_mock.route(host="yclients.invalid").respond(200, json={})

    bearer_only_client = YclientsClient(
        base_url=HOST,
        partner_token=SecretStr("fake-partner"),
        user_token=SecretStr("fake-user"),
        company_id=7,
        policy=load_studio().yclients_http,
        matrix=bearer_only_matrix,
        allow_pending=True,
        sleep=AsyncMock(),
        now=lambda: NOW,
    )
    _run(bearer_only_client, tmp_path)
    bearer_only_calls = [c for c in respx_mock.calls if "/api/v1/records/" in str(c.request.url)]
    # Adapter sends only what the matrix header template specifies.
    bearer_only_auth = bearer_only_calls[0].request.headers.get("authorization", "")
    assert "Bearer fake-partner" in bearer_only_auth
    assert "User fake-user" not in bearer_only_auth  # User token absent with bearer-only config


def test_t2_records_list_route_template_reaches_http_and_removed_staff_route_blocked(
    respx_mock, client_factory
):
    """T2: proper records.list template reaches HTTP with correct method+query;
    removed /company/{id}/staff/ candidate raises OperationDenied before any HTTP call.
    """
    from app.integrations.yclients_guards import SAFE_READ_ROUTES

    # Structural invariant: template in SAFE_READ_ROUTES matches the matrix candidate.
    expected_template = (
        "GET /api/v1/records/{company_id}?start_date={from}&end_date={to}&page={page}&count={count}"
    )
    assert expected_template in SAFE_READ_ROUTES["records.list"]

    # Positive: allowed template reaches the network with correct method and query params.
    route = respx_mock.get(url__regex=rf"{HOST}/api/v1/records/7").respond(403)

    pending_client = YclientsClient(
        base_url=HOST,
        partner_token=SecretStr("fake-partner"),
        user_token=SecretStr("fake-user"),
        company_id=7,
        policy=load_studio().yclients_http,
        matrix=load_matrix(),
        allow_pending=True,
        now=lambda: NOW,
    )

    async def scenario_allowed():
        async with pending_client as client:
            try:
                await client.request(
                    "records.list",
                    {"from": "2026-01-01", "to": "2026-01-02", "page": 1, "count": 200},
                )
            except AdapterError:
                pass  # 403 expected; we only care that the HTTP call was made

    asyncio.run(scenario_allowed())
    assert route.call_count == 1
    url_str = str(respx_mock.calls[0].request.url)
    assert respx_mock.calls[0].request.method == "GET"
    assert "/api/v1/records/7" in url_str
    assert "start_date=2026-01-01" in url_str
    assert "end_date=2026-01-02" in url_str
    assert "page=1" in url_str
    assert "count=200" in url_str

    # Negative: removed staff route is blocked by SAFE_READ_ROUTES BEFORE any HTTP attempt.
    # Insert the removed candidate back into the matrix to prove the guard is in the allowlist,
    # not the matrix candidates list.
    respx_mock.reset()
    raw = load_matrix().model_dump(mode="json")
    for op in raw["operations"]:
        if op["id"] == "staff.list":
            op["candidates"].insert(0, "GET /api/v1/company/{company_id}/staff/")
            break
    bad_matrix = ApiMatrix.model_validate(raw)

    denied_client = YclientsClient(
        base_url=HOST,
        partner_token=SecretStr("fake-partner"),
        user_token=SecretStr("fake-user"),
        company_id=7,
        policy=load_studio().yclients_http,
        matrix=bad_matrix,
        allow_pending=True,
        now=lambda: NOW,
    )

    async def scenario_denied():
        async with denied_client as client:
            await client.request("staff.list", candidate_index=0)  # removed candidate

    with pytest.raises(OperationDenied):
        asyncio.run(scenario_denied())
    assert not respx_mock.calls  # guard fires before any HTTP attempt


def test_t3_records_list_422_differs_from_403(respx_mock, client_factory):
    """T3: reversed date range (start > end) causes 422; status_code is distinct from 403.

    Calls client.request() directly — not through run_spike — to isolate adapter behaviour
    from spike orchestration.  Also verifies spike._categorize maps the error to "http_422".
    """
    respx_mock.get(url__regex=rf"{HOST}/api/v1/records/7").respond(422)

    t3_client = YclientsClient(
        base_url=HOST,
        partner_token=SecretStr("fake-partner"),
        user_token=SecretStr("fake-user"),
        company_id=7,
        policy=load_studio().yclients_http,
        matrix=load_matrix(),
        allow_pending=True,
        now=lambda: NOW,
    )

    async def scenario():
        async with t3_client as client:
            await client.request(
                "records.list",
                # start_date > end_date: simulates a bad-param 422 from the server.
                {"from": "2026-01-02", "to": "2026-01-01", "page": 1, "count": 200},
            )

    with pytest.raises(AdapterError) as exc_info:
        asyncio.run(scenario())

    assert exc_info.value.status_code == 422
    assert exc_info.value.status_code != 403  # separate from auth errors

    # Spike-layer categorization must also distinguish 422 from 403.
    assert _categorize(exc_info.value) == "http_422"
    assert _categorize(exc_info.value) != "http_403"
