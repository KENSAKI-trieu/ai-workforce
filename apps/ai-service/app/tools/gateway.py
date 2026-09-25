"""HTTP-only client for the backend internal tool gateway."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


class ToolGatewayError(RuntimeError):
    pass


class ToolGatewayClient:
    """A request-scoped client bound to a backend-issued internal JWT."""

    def __init__(self, base_url: str, internal_jwt: str) -> None:
        if not base_url.strip():
            raise ValueError("Tool gateway URL is required")
        if not internal_jwt.strip():
            raise ValueError("Internal tool JWT is required")
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {internal_jwt}"}

    @staticmethod
    def _retryable(response: httpx.Response | None, exc: Exception | None, statuses: tuple[int, ...]) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return True
        return response is not None and response.status_code in statuses

    @staticmethod
    def _result(name: str, response: httpx.Response) -> Any:
        """Read the gateway envelope, failing as a gateway error rather than a KeyError.

        A 200 that is not the expected envelope is still the gateway misbehaving, and the
        caller only knows how to handle ToolGatewayError.
        """
        try:
            return response.json()["result"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ToolGatewayError(
                f"Tool gateway returned an unreadable response for '{name}'"
            ) from exc

    def invoke(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        max_attempts: int,
        backoff_seconds: float,
        retryable_status_codes: tuple[int, ...],
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            response: httpx.Response | None = None
            try:
                response = httpx.post(
                    f"{self.base_url}/api/v1/internal/tools/{name}/invoke",
                    json={"input": payload},
                    headers=self.headers,
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                return self._result(name, response)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == max_attempts or not self._retryable(response, exc, retryable_status_codes):
                    detail = response.text[:1000] if response is not None else type(exc).__name__
                    raise ToolGatewayError(f"Tool gateway rejected '{name}': {detail}") from exc
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))
        raise ToolGatewayError(f"Tool gateway failed '{name}'") from last_error

    async def ainvoke(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        max_attempts: int,
        backoff_seconds: float,
        retryable_status_codes: tuple[int, ...],
    ) -> Any:
        last_error: Exception | None = None
        async with httpx.AsyncClient(headers=self.headers, timeout=timeout_seconds) as client:
            for attempt in range(1, max_attempts + 1):
                response: httpx.Response | None = None
                try:
                    response = await client.post(
                        f"{self.base_url}/api/v1/internal/tools/{name}/invoke",
                        json={"input": payload},
                    )
                    response.raise_for_status()
                    return self._result(name, response)
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt == max_attempts or not self._retryable(response, exc, retryable_status_codes):
                        detail = response.text[:1000] if response is not None else type(exc).__name__
                        raise ToolGatewayError(f"Tool gateway rejected '{name}': {detail}") from exc
                    await asyncio.sleep(backoff_seconds * (2 ** (attempt - 1)))
        raise ToolGatewayError(f"Tool gateway failed '{name}'") from last_error

    def list_tools(self) -> list[dict[str, Any]]:
        """The backend's contracts for the tools this caller may invoke.

        The backend filters by the actor's ACL, the AI Employee's grants and the tenant's
        plugins, so what comes back is exactly what the graph may offer the model.
        """
        try:
            response = httpx.get(
                f"{self.base_url}/api/v1/internal/tools",
                headers=self.headers,
                timeout=10.0,
            )
            response.raise_for_status()
            contracts = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolGatewayError("Tool contracts could not be loaded from the backend") from exc
        if not isinstance(contracts, list):
            raise ToolGatewayError("Tool contracts response is not a list")
        return contracts

    def record_model_usage(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/internal/tools/model-usage",
                json=payload,
                headers=self.headers,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise ToolGatewayError("Model usage telemetry was rejected") from exc

    def create_graph_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/internal/tools/langgraph-approvals",
                json=payload,
                headers=self.headers,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise ToolGatewayError("LangGraph approval registration was rejected") from exc

    async def arecord_model_usage(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/internal/tools/model-usage",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise ToolGatewayError("Model usage telemetry was rejected") from exc
