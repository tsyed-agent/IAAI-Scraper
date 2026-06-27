"""Docker wake-on-demand and API readiness orchestration."""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from typing import Optional

import httpx

from .config import McpSettings

log = logging.getLogger("iaai_mcp.orchestrator")


class ApiUnavailableError(RuntimeError):
    """Raised when the IAAI API cannot be reached after wake attempts."""


class ApiOrchestrator:
    """Ensures the IAAI API is running before requests.

    - Single-flight Docker wake: parallel callers share one ``docker compose up``.
    - Readiness gate: waits for authenticated ``/readyz`` before releasing waiters.
    - Connection pooling is handled by the shared ``httpx.AsyncClient``.
    """

    def __init__(self, settings: McpSettings) -> None:
        self.settings = settings
        self._wake_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._client: Optional[httpx.AsyncClient] = None
        self._request_sem = asyncio.Semaphore(settings.max_concurrent_requests)

    @property
    def request_semaphore(self) -> asyncio.Semaphore:
        return self._request_sem

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.api_token}"}

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_connections=self.settings.connection_pool_size,
                max_keepalive_connections=self.settings.connection_pool_size,
            )
            self._client = httpx.AsyncClient(
                base_url=self.settings.api_base_url,
                headers=self._auth_headers(),
                timeout=httpx.Timeout(self.settings.request_timeout_s),
                limits=limits,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def ping_health(self) -> bool:
        try:
            client = await self.get_client()
            r = await client.get("/healthz")
            return r.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    async def ping_ready(self) -> bool:
        try:
            client = await self.get_client()
            r = await client.get("/readyz")
            return r.status_code == 200 and r.json().get("status") == "ready"
        except (httpx.HTTPError, OSError, ValueError):
            return False

    def _docker_available(self) -> bool:
        return shutil.which("docker") is not None

    def _compose_cmd(self, *args: str) -> list[str]:
        return [
            "docker", "compose",
            "-f", str(self.settings.docker_compose_file),
            *args,
        ]

    async def _run_compose(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = self._compose_cmd(*args)
        log.info("Running: %s", " ".join(cmd))

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.settings.docker_wake_timeout_s,
            )

        return await asyncio.to_thread(_run)

    async def _start_docker_service(self) -> None:
        if not self._docker_available():
            raise ApiUnavailableError("docker CLI not found; cannot wake IAAI API container")
        if not self.settings.docker_compose_file.is_file():
            raise ApiUnavailableError(
                f"docker-compose file not found: {self.settings.docker_compose_file}"
            )
        result = await self._run_compose("up", "-d", self.settings.docker_service)
        if result.returncode != 0:
            raise ApiUnavailableError(
                f"docker compose up failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    async def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self.settings.docker_wake_timeout_s
        while time.monotonic() < deadline:
            if await self.ping_ready():
                self._ready.set()
                log.info("IAAI API ready at %s", self.settings.api_base_url)
                return
            if await self.ping_health():
                log.debug("API healthz OK, waiting for readyz...")
            await asyncio.sleep(self.settings.docker_poll_interval_s)
        raise ApiUnavailableError(
            f"IAAI API did not become ready within {self.settings.docker_wake_timeout_s}s"
        )

    async def ensure_ready(self) -> None:
        """Make sure the API accepts authenticated requests."""
        if self._ready.is_set() and await self.ping_ready():
            return

        self._ready.clear()

        if await self.ping_ready():
            self._ready.set()
            return

        if not self.settings.docker_wake:
            raise ApiUnavailableError(
                f"IAAI API unavailable at {self.settings.api_base_url} "
                "(IAAI_DOCKER_WAKE=false)"
            )

        async with self._wake_lock:
            if await self.ping_ready():
                self._ready.set()
                return
            log.warning(
                "IAAI API down — starting Docker service %s",
                self.settings.docker_service,
            )
            await self._start_docker_service()
            await self._wait_until_ready()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        """Authenticated API request with wake-on-demand and retries."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.settings.max_retries + 1):
            async with self._request_sem:
                try:
                    await self.ensure_ready()
                    client = await self.get_client()
                    response = await client.request(
                        method,
                        path,
                        params=params,
                        json=json_body,
                    )
                    if response.status_code >= 500:
                        self._ready.clear()
                        last_error = httpx.HTTPStatusError(
                            f"server error {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                        continue
                    return response
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                    self._ready.clear()
                    last_error = e
                    log.warning("API request failed (attempt %s/%s): %s", attempt, self.settings.max_retries, e)
                    if attempt < self.settings.max_retries:
                        await asyncio.sleep(min(2 ** attempt, 8))
        raise ApiUnavailableError(
            f"API request failed after {self.settings.max_retries} attempts: {last_error}"
        ) from last_error

    async def get_json(self, path: str, *, params: dict | None = None) -> dict:
        r = await self.request("GET", path, params=params)
        r.raise_for_status()
        return r.json()

    async def post_json(self, path: str, *, json_body: dict | None = None) -> dict:
        r = await self.request("POST", path, json_body=json_body or {})
        r.raise_for_status()
        return r.json()
