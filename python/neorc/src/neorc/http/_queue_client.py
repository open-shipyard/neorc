# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The queue client publishers and workers use to reach the manager over HTTP."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import TracebackType
from typing import Any

import httpx

from neorc_core import (
    ManagerUnavailableError,
    Payload,
    QueueClient,
    Task,
    TaskId,
    TaskNotFoundError,
    TaskStateError,
    TaskStatus,
)
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS

DEFAULT_POLL_TIMEOUT = 30.0

_POLL_GRACE = 5.0
"""Wait this much longer than the manager, so its own deadline answers first."""


class HttpQueueClient(QueueClient):
    """Talks to the manager service over HTTP/JSON.

    ``pick_next_task`` long-polls: the request stays open until a task is ready
    or the manager gives up, whichever comes first. The client-side timeout must
    outlast the manager's own deadline.
    """

    def __init__(
        self,
        manager_address: str,
        *,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._manager_address = manager_address
        self._poll_timeout = poll_timeout
        self._client = httpx.AsyncClient(
            base_url=_base_url(manager_address),
            timeout=httpx.Timeout(10.0, read=poll_timeout + _POLL_GRACE),
            transport=transport,
        )

    async def __aenter__(self) -> HttpQueueClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the underlying connection pool."""
        await self._client.aclose()

    async def publish(
        self,
        name: str,
        payload: Payload,
        *,
        run_after: datetime | None = None,
        priority: int = 0,
    ) -> TaskId:
        body = {
            "name": name,
            "payload": dict(payload),
            "run_after": run_after.isoformat() if run_after is not None else None,
            "priority": priority,
        }
        response = await self._request("POST", "/tasks", json=body)
        return uuid.UUID(response.json()["id"])

    async def pick_next_task(
        self, *, timeout: float, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        response = await self._request(
            "POST",
            "/tasks/next",
            params={"timeout": timeout, "lease_seconds": lease_seconds},
            timeout=httpx.Timeout(10.0, read=timeout + _POLL_GRACE),
        )
        if response.status_code == httpx.codes.NO_CONTENT:
            return None
        return _to_task(response.json())

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        response = await self._request(
            "POST",
            f"/tasks/{task_id}/heartbeat",
            json={"lease_seconds": lease_seconds},
        )
        return datetime.fromisoformat(response.json()["lease_expires_at"])

    async def report_started(self, task_id: TaskId) -> None:
        await self._request("POST", f"/tasks/{task_id}/started")

    async def report_finished(
        self, task_id: TaskId, *, error: str | None = None
    ) -> None:
        await self._request("POST", f"/tasks/{task_id}/finished", json={"error": error})

    async def get_status(self, task_id: TaskId) -> TaskStatus:
        response = await self._request("GET", f"/tasks/{task_id}")
        return TaskStatus(response.json()["status"])

    async def get_task(self, task_id: TaskId) -> Task:
        """Fetch a whole task, for publishers that want more than its status."""
        response = await self._request("GET", f"/tasks/{task_id}")
        return _to_task(response.json())

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request, turning transport and status failures into neorc errors."""
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ManagerUnavailableError(
                f"{method} {self._manager_address}{path}: {exc}"
            ) from exc
        _raise_for_status(response)
        return response


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    detail = _detail(response)
    if response.status_code == httpx.codes.NOT_FOUND:
        raise TaskNotFoundError(detail)
    if response.status_code == httpx.codes.CONFLICT:
        raise TaskStateError(detail)
    raise ManagerUnavailableError(
        f"the manager answered {response.status_code}: {detail}"
    )


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    return str(body.get("detail", body)) if isinstance(body, dict) else str(body)


def _to_task(body: dict[str, Any]) -> Task:
    return Task(
        id=uuid.UUID(body["id"]),
        name=body["name"],
        payload=body["payload"],
        status=TaskStatus(body["status"]),
        created_at=datetime.fromisoformat(body["created_at"]),
        run_after=datetime.fromisoformat(body["run_after"]),
        priority=body["priority"],
        attempts=body["attempts"],
        lease_expires_at=(
            datetime.fromisoformat(body["lease_expires_at"])
            if body.get("lease_expires_at")
            else None
        ),
        error=body.get("error"),
    )


def _base_url(manager_address: str) -> str:
    """Accept a bare host, a host:port, or a full URL."""
    if "://" in manager_address:
        return manager_address.rstrip("/")
    return f"http://{manager_address.rstrip('/')}"
