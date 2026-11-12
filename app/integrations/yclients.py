"""Read-only transport. No credentials are loaded here; all responses are sanitized."""

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from string import Formatter
from urllib.parse import quote, unquote, urlsplit

import httpx
from pydantic import JsonValue, SecretStr

from app.integrations.yclients_policy import (
    AdapterError,
    ApiMatrix,
    OperationDenied,
    load_matrix,
    redact,
)
from app.settings import YclientsHTTP


class _NoTransportPayloads(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False


class YclientsClient:
    def __init__(
        self,
        *,
        base_url: str,
        partner_token: SecretStr,
        user_token: SecretStr,
        company_id: int,
        policy: YclientsHTTP,
        matrix: ApiMatrix | None = None,
        allow_pending: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        base = httpx.URL(base_url)
        if (
            base.scheme != "https"
            or not base.host
            or base.userinfo
            or base.query
            or base.fragment
            or base.path != "/"
            or company_id <= 0
        ):
            raise OperationDenied
        self._base = base
        self._company_id = company_id
        self._matrix = matrix if matrix is not None else load_matrix()
        self._policy = policy
        self._allow_pending = allow_pending
        self._sleep, self._now = sleep, now
        try:
            headers = {
                key: value.format(
                    partner_token=partner_token.get_secret_value(),
                    user_token=user_token.get_secret_value(),
                )
                for key, value in self._matrix.meta.headers_candidates.items()
            }
            self._http = httpx.AsyncClient(
                headers=headers,
                timeout=policy.timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            )
        except (ValueError, KeyError):
            raise AdapterError from None
        # HTTPX INFO includes full query strings, httpcore DEBUG may include headers.
        # Suppress both library namespaces (also their child loggers) for this process.
        for name in (
            "httpx",
            "httpcore",
            "httpcore.connection",
            "httpcore.http11",
            "httpcore.http2",
            "httpcore.proxy",
            "httpcore.socks",
        ):
            logger = logging.getLogger(name)
            if not any(isinstance(item, _NoTransportPayloads) for item in logger.filters):
                logger.addFilter(_NoTransportPayloads())

    async def __aenter__(self) -> "YclientsClient":
        return self

    async def __aexit__(self, *_exc) -> None:
        await self._http.aclose()

    def _route(
        self, operation_id: str, values: Mapping[str, str | int], candidate_index: int | None
    ):
        operation = self._matrix.resolve(operation_id, allow_pending=self._allow_pending)
        if operation.status == "verified":
            if candidate_index is not None or operation.path is None:
                raise OperationDenied
            template = operation.path
        else:
            index = candidate_index if candidate_index is not None else 0
            if index < 0 or index >= len(operation.candidates):
                raise OperationDenied
            template = operation.candidates[index]
        try:
            method, target = template.split(" ", 1)
            if method not in {"GET", "POST"}:
                raise OperationDenied
            inputs = {**values, "company_id": self._company_id}
            for _, field, spec, conversion in Formatter().parse(target):
                if field is not None and (field not in inputs or spec or conversion):
                    raise OperationDenied
            target = target.format_map(
                {key: quote(str(value), safe="") for key, value in inputs.items()}
            )
            parsed = urlsplit(target)
            path = unquote(parsed.path)
            if (
                not target.startswith("/")
                or target.startswith("//")
                or parsed.scheme
                or parsed.netloc
                or parsed.fragment
                or "\\" in path
                or any(part in {".", ".."} for part in path.split("/"))
            ):
                raise OperationDenied
            return method, self._base.join(target)
        except (ValueError, KeyError, IndexError):
            raise OperationDenied from None

    def _delay(self, response: httpx.Response | None, attempt: int) -> float:
        delay = self._policy.retry_backoff_seconds[attempt]
        header = response.headers.get("Retry-After") if response is not None else None
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                try:
                    retry_after = (parsedate_to_datetime(header) - self._now()).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    retry_after = delay
            if math.isfinite(retry_after):
                delay = max(delay, retry_after)
            if delay > self._policy.max_retry_delay_seconds:
                raise AdapterError(
                    status_code=response.status_code if response is not None else None
                )
        return delay

    async def request(
        self,
        operation_id: str,
        values: Mapping[str, str | int] | None = None,
        *,
        candidate_index: int | None = None,
        body: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        method, url = self._route(operation_id, values or {}, candidate_index)
        if body is not None and method != "POST":
            raise OperationDenied
        for attempt in range(len(self._policy.retry_backoff_seconds) + 1):
            response = None
            try:
                response = await self._http.request(method, url, json=body)
            except httpx.TransportError:
                pass
            if response is not None:
                status = response.status_code
                if response.is_success:
                    try:
                        return redact(response.json())
                    except (json.JSONDecodeError, UnicodeError):
                        raise AdapterError(status_code=status) from None
                if status != 429 and not response.is_server_error:
                    raise AdapterError(status_code=status)
            if attempt == len(self._policy.retry_backoff_seconds):
                raise AdapterError(
                    status_code=response.status_code if response is not None else None
                )
            await self._sleep(self._delay(response, attempt))
        raise AdapterError
