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
from pydantic import SecretStr

from app.integrations.yclients import YclientsClient
from app.integrations.yclients_policy import AdapterError, load_matrix
from app.settings import load_studio
from scripts import spike_readonly
from scripts.spike_readonly import main, run_spike

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
    # staff.list: first candidate 404, second candidate ok.
    respx_mock.get(url__regex=rf"{HOST}/api/v1/company/7/staff").respond(404)
    _ok(respx_mock.get(url__regex=rf"{HOST}/api/v1/staff/7"))
    respx_mock.route(host="yclients.invalid").respond(500)  # everything else fails

    report = _run(client_factory(sleep=AsyncMock()), tmp_path)
    staff = next(r for r in report["operations"] if r["operation_id"] == "staff.list")
    assert staff["probed_ok"] is True
    assert staff["proposed_candidate_index"] == 1
    assert [a["category"] for a in staff["attempts"]] == ["http_404", "ok"]


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
