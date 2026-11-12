"""Container runtime abstraction with hardened Docker Engine & deterministic Mock adapters."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import Settings
from app.core.exceptions import DockerRuntimeError


@dataclass(frozen=True, slots=True)
class ContainerCreateSpec:
    container_name: str
    image: str
    workspace_path: str
    cpu_limit: float
    memory_limit_mb: int
    pids_limit: int
    network_disabled: bool
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContainerRuntimeInfo:
    container_id: str
    container_name: str
    status: str
    spec: ContainerCreateSpec


class DockerRuntimeAdapter(Protocol):
    """Protocol implemented by both Docker Engine and deterministic test/local adapters."""

    async def ping(self) -> bool: ...

    async def create_container(self, spec: ContainerCreateSpec) -> ContainerRuntimeInfo: ...

    async def start_container(self, container_id: str) -> None: ...

    async def stop_container(self, container_id: str, timeout: int = 10) -> None: ...

    async def remove_container(self, container_id: str, force: bool = True) -> None: ...

    async def get_logs(self, container_id: str, tail: int = 200) -> list[str]: ...


class MockDockerAdapter:
    """In-memory deterministic container adapter enforcing full runtime contract & fault injection."""

    def __init__(self) -> None:
        self.containers: dict[str, dict[str, Any]] = {}
        self.fail_on_ping: bool = False
        self.fail_on_create: bool = False
        self.fail_on_start: bool = False
        self.fail_on_stop: bool = False
        self.fail_on_remove: bool = False
        self.operations: list[tuple[str, str]] = []

    async def ping(self) -> bool:
        return not self.fail_on_ping

    async def create_container(self, spec: ContainerCreateSpec) -> ContainerRuntimeInfo:
        if self.fail_on_create:
            raise DockerRuntimeError("Simulated container creation failure.")
        if spec.memory_limit_mb < 64 or spec.pids_limit < 16 or spec.cpu_limit <= 0:
            raise DockerRuntimeError("Invalid container resource limit configuration.")
        container_id = f"mock-{uuid.uuid4().hex[:16]}"
        self.containers[container_id] = {
            "id": container_id,
            "name": spec.container_name,
            "status": "created",
            "spec": spec,
            "logs": [
                f"[init] sandbox container '{spec.container_name}' created with image={spec.image}",
                f"[security] network_disabled={spec.network_disabled} pids={spec.pids_limit} mem={spec.memory_limit_mb}m cpu={spec.cpu_limit}",
            ],
        }
        self.operations.append(("create", container_id))
        return ContainerRuntimeInfo(
            container_id=container_id,
            container_name=spec.container_name,
            status="created",
            spec=spec,
        )

    async def start_container(self, container_id: str) -> None:
        if self.fail_on_start:
            raise DockerRuntimeError("Simulated container start failure.")
        entry = self.containers.get(container_id)
        if entry is None:
            raise DockerRuntimeError(f"Container {container_id} does not exist.")
        entry["status"] = "running"
        entry["logs"].append(f"[runtime] container {container_id} started")
        self.operations.append(("start", container_id))

    async def stop_container(self, container_id: str, timeout: int = 10) -> None:
        if self.fail_on_stop:
            raise DockerRuntimeError("Simulated container stop failure.")
        entry = self.containers.get(container_id)
        if entry is None:
            return
        entry["status"] = "stopped"
        entry["logs"].append(f"[runtime] container {container_id} stopped cleanly")
        self.operations.append(("stop", container_id))

    async def remove_container(self, container_id: str, force: bool = True) -> None:
        if self.fail_on_remove:
            raise DockerRuntimeError("Simulated container removal failure.")
        self.containers.pop(container_id, None)
        self.operations.append(("remove", container_id))

    async def get_logs(self, container_id: str, tail: int = 200) -> list[str]:
        entry = self.containers.get(container_id)
        if entry is None:
            return []
        lines: list[str] = list(entry["logs"])
        return lines[-tail:]


class RealDockerAdapter:
    """Docker SDK adapter with strict non-root isolation, resource limits, and disabled networking."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    def _get_docker_client(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    async def ping(self) -> bool:
        try:
            client = self._get_docker_client()
            return bool(await asyncio.to_thread(client.ping))
        except Exception:
            return False

    async def create_container(self, spec: ContainerCreateSpec) -> ContainerRuntimeInfo:
        def _create() -> str:
            client = self._get_docker_client()
            container = client.containers.create(
                image=spec.image,
                command=["sleep", "infinity"],
                name=spec.container_name,
                detach=True,
                working_dir="/workspace",
                volumes={spec.workspace_path: {"bind": "/workspace", "mode": "rw"}},
                network_disabled=spec.network_disabled,
                mem_limit=f"{spec.memory_limit_mb}m",
                memswap_limit=f"{spec.memory_limit_mb}m",
                nano_cpus=int(spec.cpu_limit * 1_000_000_000),
                pids_limit=spec.pids_limit,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                user="65534:65534",
                labels=spec.labels,
            )
            return str(container.id)

        try:
            container_id = await asyncio.to_thread(_create)
        except Exception as exc:
            raise DockerRuntimeError("Failed to create isolated workspace container.") from exc
        return ContainerRuntimeInfo(
            container_id=container_id,
            container_name=spec.container_name,
            status="created",
            spec=spec,
        )

    async def start_container(self, container_id: str) -> None:
        def _start() -> None:
            client = self._get_docker_client()
            container = client.containers.get(container_id)
            container.start()

        try:
            await asyncio.to_thread(_start)
        except Exception as exc:
            raise DockerRuntimeError("Failed to start isolated workspace container.") from exc

    async def stop_container(self, container_id: str, timeout: int = 10) -> None:
        def _stop() -> None:
            from docker.errors import NotFound

            client = self._get_docker_client()
            try:
                container = client.containers.get(container_id)
                container.stop(timeout=timeout)
            except NotFound:
                return

        try:
            await asyncio.to_thread(_stop)
        except Exception as exc:
            raise DockerRuntimeError("Failed to stop isolated workspace container.") from exc

    async def remove_container(self, container_id: str, force: bool = True) -> None:
        def _remove() -> None:
            from docker.errors import NotFound

            client = self._get_docker_client()
            try:
                container = client.containers.get(container_id)
                container.remove(force=force)
            except NotFound:
                return

        try:
            await asyncio.to_thread(_remove)
        except Exception as exc:
            raise DockerRuntimeError("Failed to remove isolated workspace container.") from exc

    async def get_logs(self, container_id: str, tail: int = 200) -> list[str]:
        def _logs() -> list[str]:
            from docker.errors import NotFound

            client = self._get_docker_client()
            try:
                container = client.containers.get(container_id)
                raw = container.logs(tail=tail, timestamps=False)
                text = raw.decode("utf-8", errors="replace")
                return [line[:500] for line in text.splitlines()][-tail:]
            except NotFound:
                return []

        try:
            return await asyncio.to_thread(_logs)
        except Exception as exc:
            raise DockerRuntimeError("Failed to retrieve workspace container logs.") from exc


def build_docker_adapter(settings: Settings) -> DockerRuntimeAdapter:
    """Instantiate configured Docker runtime adapter."""
    if settings.WORKSPACE_DOCKER_MODE == "docker":
        return RealDockerAdapter(settings)
    return MockDockerAdapter()

