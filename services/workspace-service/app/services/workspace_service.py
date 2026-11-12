"""Core Workspace provisioning, container lifecycle, refresh, logs, and cleanup orchestration."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.github_client import GitHubServiceClient
from app.clients.project_client import ProjectClient, assert_workspace_access
from app.core.config import Settings
from app.core.dependencies import AuthContext
from app.core.exceptions import (
    ConflictError,
    DockerRuntimeError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
    sanitize_error_message,
)
from app.core.logging import MetricsRegistry, get_logger
from app.core.security import UserRole, validate_git_ref
from app.models.base import utc_now
from app.models.workspace import TERMINAL_WORKSPACE_STATUSES, Workspace, WorkspaceStatus
from app.repositories.workspace_repository import WorkspaceRepository
from app.schemas.workspace import (
    WorkspaceCleanupResponse,
    WorkspaceDetailResponse,
    WorkspaceListResponse,
    WorkspaceLogsResponse,
    WorkspaceProvisionRequest,
    WorkspaceRefreshRequest,
    WorkspaceResponse,
    WorkspaceTestRunRequest,
    WorkspaceTestRunResponse,
)
from app.services.archive_service import ArchiveExtractor
from app.services.docker_adapter import ContainerCreateSpec, DockerRuntimeAdapter
from app.services.workspace_state_service import WorkspaceStateService

logger = get_logger("services.workspace")

_ELIGIBLE_PROJECT_STATUSES = frozenset({"ASSIGNED", "IN_PROGRESS", "CHANGES_REQUESTED"})


class WorkspaceService:
    """Coordinates isolated workspace provisioning and lifecycle safely."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        project_client: ProjectClient,
        github_client: GitHubServiceClient,
        docker_adapter: DockerRuntimeAdapter,
        metrics: MetricsRegistry,
    ) -> None:
        self._session = session
        self._settings = settings
        self._project_client = project_client
        self._github_client = github_client
        self._docker = docker_adapter
        self._metrics = metrics
        self._repo = WorkspaceRepository(session)
        self._extractor = ArchiveExtractor(settings)

    def _resolve_workspace_dir(self, workspace_id: uuid.UUID) -> Path:
        root = Path(self._settings.WORKSPACE_STORAGE_ROOT).resolve()
        root.mkdir(parents=True, exist_ok=True)
        ws_dir = (root / str(workspace_id)).resolve()
        if not ws_dir.is_relative_to(root):
            raise ValidationError("Invalid workspace directory path.")
        return ws_dir

    async def _require_authorized_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
        allow_admin_read: bool = False,
        include_events: bool = False,
    ) -> Workspace:
        ws = await self._repo.get_by_id(workspace_id, include_events=include_events)
        if ws is None:
            raise NotFoundError("Workspace not found.", error_code="WORKSPACE_NOT_FOUND")

        project = await self._project_client.get_project(
            ws.project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        assert_workspace_access(project, auth_ctx.user, allow_admin_read=allow_admin_read)
        return ws

    async def provision_workspace(
        self,
        *,
        project_id: uuid.UUID,
        payload: WorkspaceProvisionRequest,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> WorkspaceResponse:
        validated_ref = validate_git_ref(payload.ref)
        image = payload.runtime_image or self._settings.WORKSPACE_DEFAULT_IMAGE
        if image not in self._settings.allowed_image_set:
            raise ValidationError(
                f"Unsupported runtime image '{image}'. Must be one of allowlisted images."
            )

        project = await self._project_client.get_project(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        assert_workspace_access(project, auth_ctx.user, allow_admin_read=False)

        if project.status.upper() not in _ELIGIBLE_PROJECT_STATUSES:
            raise ConflictError(
                f"Cannot provision workspace while project is in {project.status} status.",
                error_code="INVALID_PROJECT_STATUS",
            )

        existing_active = await self._repo.get_active_by_project_id(project_id)
        if existing_active is not None:
            raise ConflictError(
                "An active workspace already exists for this project.",
                error_code="WORKSPACE_ALREADY_EXISTS",
            )

        user_active_count = await self._repo.count_active_by_user(auth_ctx.user.id)
        if user_active_count >= self._settings.WORKSPACE_MAX_ACTIVE_PER_USER:
            raise ConflictError(
                "Maximum concurrent active workspaces reached for this user.",
                error_code="WORKSPACE_QUOTA_EXCEEDED",
            )

        snapshot = await self._github_client.fetch_repository_snapshot(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
            ref=validated_ref,
        )

        now = utc_now()
        expires_at = now + timedelta(hours=self._settings.WORKSPACE_TTL_HOURS)
        ws_id = uuid.uuid4()
        ws_dir = self._resolve_workspace_dir(ws_id)
        container_name = f"cb-ws-{ws_id.hex[:16]}"

        try:
            ws = await self._repo.create_workspace(
                project_id=project_id,
                owner_id=project.owner_id,
                assigned_freelancer_id=project.assigned_freelancer_id,
                created_by=auth_ctx.user.id,
                repository_full_name=snapshot.full_name,
                repository_ref=snapshot.ref,
                container_name=container_name,
                workspace_path=ws_dir.as_posix(),
                runtime_image=image,
                cpu_limit=f"{self._settings.WORKSPACE_CPU_LIMIT}",
                memory_limit_mb=self._settings.WORKSPACE_MEMORY_LIMIT_MB,
                pids_limit=self._settings.WORKSPACE_PIDS_LIMIT,
                network_disabled=self._settings.WORKSPACE_NETWORK_DISABLED,
                expires_at=expires_at,
                now=now,
            )
            ws.id = ws_id
            ws_dir_actual = self._resolve_workspace_dir(ws.id)
            ws.workspace_path = ws_dir_actual.as_posix()
            ws.container_name = f"cb-ws-{ws.id.hex[:16]}"
            await self._repo.add_event(
                workspace_id=ws.id,
                actor_id=auth_ctx.user.id,
                event_type="PROVISION_STARTED",
                previous_status=None,
                new_status=WorkspaceStatus.PROVISIONING.value,
                details={"repository": snapshot.full_name, "ref": snapshot.ref},
                now=now,
            )
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(
                "An active workspace already exists for this project.",
                error_code="WORKSPACE_ALREADY_EXISTS",
            ) from exc

        created_container_id: str | None = None
        try:
            extracted = self._extractor.extract_zip_bytes(
                snapshot.archive_bytes,
                destination_dir=Path(ws.workspace_path),
            )
            container_info = await self._docker.create_container(
                ContainerCreateSpec(
                    container_name=ws.container_name,
                    image=ws.runtime_image,
                    workspace_path=ws.workspace_path,
                    cpu_limit=self._settings.WORKSPACE_CPU_LIMIT,
                    memory_limit_mb=ws.memory_limit_mb,
                    pids_limit=ws.pids_limit,
                    network_disabled=ws.network_disabled,
                    labels={
                        "codebridge.workspace_id": str(ws.id),
                        "codebridge.project_id": str(project_id),
                    },
                )
            )
            created_container_id = container_info.container_id

            target_status = WorkspaceStatus.READY
            started_at = None
            if payload.auto_start:
                await self._docker.start_container(created_container_id)
                target_status = WorkspaceStatus.RUNNING
                started_at = utc_now()

            done_at = utc_now()
            ws.snapshot_sha256 = extracted.archive_sha256
            ws.file_count = extracted.file_count
            ws.storage_bytes = extracted.total_bytes
            ws.container_id = created_container_id
            ws.status = target_status
            ws.started_at = started_at
            ws.updated_at = done_at
            await self._repo.add_event(
                workspace_id=ws.id,
                actor_id=auth_ctx.user.id,
                event_type="PROVISION_COMPLETED" if not payload.auto_start else "PROVISION_AND_STARTED",
                previous_status=WorkspaceStatus.PROVISIONING.value,
                new_status=target_status.value,
                details={"file_count": extracted.file_count, "storage_bytes": extracted.total_bytes},
                now=done_at,
            )
            await self._session.commit()
            self._metrics.increment("workspaces_provisioned")
            if payload.auto_start:
                self._metrics.increment("workspaces_started")

            if project.status.upper() in {"ASSIGNED", "CHANGES_REQUESTED"}:
                await self._project_client.mark_project_in_progress(
                    project_id,
                    bearer_token=auth_ctx.bearer_token,
                    request_id=request_id,
                )
            return WorkspaceResponse.model_validate(ws)
        except Exception as exc:
            if created_container_id is not None:
                try:
                    await self._docker.remove_container(created_container_id, force=True)
                except Exception:
                    pass
            try:
                self._extractor.remove_workspace_directory(Path(ws.workspace_path))
            except Exception:
                pass

            fail_ts = utc_now()
            ws.status = WorkspaceStatus.FAILED
            ws.last_error = sanitize_error_message(str(exc), fallback="Provisioning failed.")
            ws.updated_at = fail_ts
            await self._repo.add_event(
                workspace_id=ws.id,
                actor_id=auth_ctx.user.id,
                event_type="PROVISION_FAILED",
                previous_status=WorkspaceStatus.PROVISIONING.value,
                new_status=WorkspaceStatus.FAILED.value,
                details={"error": ws.last_error},
                now=fail_ts,
            )
            await self._session.commit()
            self._metrics.increment("workspace_failures")
            raise

    async def list_project_workspaces(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> WorkspaceListResponse:
        project = await self._project_client.get_project(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        assert_workspace_access(project, auth_ctx.user, allow_admin_read=True)
        items = await self._repo.list_by_project_id(project_id)
        return WorkspaceListResponse(
            items=[WorkspaceResponse.model_validate(w) for w in items],
            total=len(items),
        )

    async def get_workspace_detail(
        self,
        *,
        workspace_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> WorkspaceDetailResponse:
        ws = await self._require_authorized_workspace(
            workspace_id=workspace_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
            allow_admin_read=True,
            include_events=True,
        )
        return WorkspaceDetailResponse.model_validate(ws)

    async def start_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> WorkspaceResponse:
        ws = await self._require_authorized_workspace(
            workspace_id=workspace_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
            allow_admin_read=False,
        )
        now = utc_now()
        if ws.expires_at.replace(tzinfo=now.tzinfo) <= now if ws.expires_at.tzinfo is None else ws.expires_at <= now:
            await self._expire_workspace(ws, actor_id=auth_ctx.user.id)
            raise ConflictError("Workspace has expired.", error_code="WORKSPACE_EXPIRED")

        WorkspaceStateService.assert_transition(ws.status, WorkspaceStatus.RUNNING)
        if not ws.container_id:
            raise DockerRuntimeError("Workspace has no provisioned container.")

        await self._docker.start_container(ws.container_id)
        prev = ws.status.value
        ws.status = WorkspaceStatus.RUNNING
        ws.started_at = now
        ws.updated_at = now
        await self._repo.add_event(
            workspace_id=ws.id,
            actor_id=auth_ctx.user.id,
            event_type="WORKSPACE_STARTED",
            previous_status=prev,
            new_status=WorkspaceStatus.RUNNING.value,
            now=now,
        )
        await self._session.commit()
        self._metrics.increment("workspaces_started")
        return WorkspaceResponse.model_validate(ws)

    async def stop_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> WorkspaceResponse:
        ws = await self._require_authorized_workspace(
            workspace_id=workspace_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
            allow_admin_read=False,
        )
        WorkspaceStateService.assert_transition(ws.status, WorkspaceStatus.STOPPED)
        if ws.container_id:
            await self._docker.stop_container(ws.container_id)

        now = utc_now()
        prev = ws.status.value
        ws.status = WorkspaceStatus.STOPPED
        ws.stopped_at = now
        ws.updated_at = now
        await self._repo.add_event(
            workspace_id=ws.id,
            actor_id=auth_ctx.user.id,
            event_type="WORKSPACE_STOPPED",
            previous_status=prev,
            new_status=WorkspaceStatus.STOPPED.value,
            now=now,
        )
        await self._session.commit()
        self._metrics.increment("workspaces_stopped")
        return WorkspaceResponse.model_validate(ws)

    async def refresh_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        payload: WorkspaceRefreshRequest,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> WorkspaceResponse:
        ws = await self._require_authorized_workspace(
            workspace_id=workspace_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
            allow_admin_read=False,
        )
        if ws.status in TERMINAL_WORKSPACE_STATUSES or ws.status == WorkspaceStatus.PROVISIONING:
            raise ConflictError(
                f"Cannot refresh repository files while workspace is {ws.status.value}.",
                error_code="INVALID_WORKSPACE_STATE",
            )
        validated_ref = validate_git_ref(payload.ref) or ws.repository_ref
        snapshot = await self._github_client.fetch_repository_snapshot(
            ws.project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
            ref=validated_ref,
        )

        ws_dir = Path(ws.workspace_path)
        self._extractor.clear_directory_contents(ws_dir)
        extracted = self._extractor.extract_zip_bytes(snapshot.archive_bytes, destination_dir=ws_dir)

        now = utc_now()
        ws.repository_ref = snapshot.ref
        ws.snapshot_sha256 = extracted.archive_sha256
        ws.file_count = extracted.file_count
        ws.storage_bytes = extracted.total_bytes
        ws.last_refreshed_at = now
        ws.updated_at = now
        await self._repo.add_event(
            workspace_id=ws.id,
            actor_id=auth_ctx.user.id,
            event_type="WORKSPACE_REFRESHED",
            previous_status=ws.status.value,
            new_status=ws.status.value,
            details={"ref": snapshot.ref, "file_count": extracted.file_count},
            now=now,
        )
        await self._session.commit()
        self._metrics.increment("workspaces_refreshed")
        return WorkspaceResponse.model_validate(ws)

    async def get_logs(
        self,
        *,
        workspace_id: uuid.UUID,
        tail: int,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> WorkspaceLogsResponse:
        ws = await self._require_authorized_workspace(
            workspace_id=workspace_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
            allow_admin_read=True,
        )
        capped_tail = max(1, min(tail, self._settings.WORKSPACE_LOG_TAIL_LINES))
        raw_lines = (
            await self._docker.get_logs(ws.container_id, tail=capped_tail)
            if ws.container_id
            else []
        )
        sanitized_lines = [sanitize_error_message(line, fallback="") for line in raw_lines]
        return WorkspaceLogsResponse(
            workspace_id=ws.id,
            status=ws.status,
            tail=capped_tail,
            lines=sanitized_lines,
            truncated=len(raw_lines) >= capped_tail,
        )

    async def run_workspace_tests(
        self,
        *,
        workspace_id: uuid.UUID,
        payload: WorkspaceTestRunRequest,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> WorkspaceTestRunResponse:
        ws = await self._require_authorized_workspace(
            workspace_id=workspace_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
            allow_admin_read=False,
        )
        now = utc_now()
        if ws.expires_at.replace(tzinfo=now.tzinfo) <= now if ws.expires_at.tzinfo is None else ws.expires_at <= now:
            await self._expire_workspace(ws, actor_id=auth_ctx.user.id)
            raise ConflictError("Workspace has expired.", error_code="WORKSPACE_EXPIRED")

        if ws.status not in {WorkspaceStatus.READY, WorkspaceStatus.RUNNING}:
            raise ConflictError(
                f"Workspace must be READY or RUNNING to run tests (current: {ws.status.value}).",
                error_code="INVALID_WORKSPACE_STATE",
            )

        ws_dir = Path(ws.workspace_path).resolve()
        if not ws_dir.exists() or not ws_dir.is_dir():
            raise ConflictError(
                "Workspace filesystem snapshot is not available.",
                error_code="WORKSPACE_DIRECTORY_MISSING",
            )

        resolved_target: Path | None = None
        if payload.target_path:
            resolved_target = (ws_dir / payload.target_path).resolve()
            if not resolved_target.is_relative_to(ws_dir) or not resolved_target.exists():
                raise ValidationError("target_path does not exist within workspace directory.")

        if payload.runner == "pytest":
            cmd = [sys.executable, "-m", "pytest", "-q"]
            if resolved_target is not None:
                cmd.append(str(resolved_target))
        elif payload.runner == "unittest":
            if resolved_target is not None and resolved_target.is_file():
                cmd = [sys.executable, "-m", "unittest", str(resolved_target)]
            elif resolved_target is not None:
                cmd = [sys.executable, "-m", "unittest", "discover", "-s", str(resolved_target)]
            else:
                cmd = [sys.executable, "-m", "unittest", "discover"]
        else:
            npm_bin = shutil.which("npm")
            if not npm_bin:
                raise ValidationError("npm runner is not available in this environment.")
            cmd = [npm_bin, "test", "--", "--watchAll=false"]

        safe_env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "TEMP": os.environ.get("TEMP", ""),
            "TMP": os.environ.get("TMP", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }

        started = time.perf_counter()
        timed_out = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(ws_dir),
                env=safe_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=float(payload.timeout_seconds),
                )
                exit_code = int(proc.returncode or 0)
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                stdout_bytes, _ = await proc.communicate()
                exit_code = 124
        except OSError as exc:
            raise DockerRuntimeError("Failed to start workspace test runner.") from exc

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        raw_output = (stdout_bytes or b"").decode("utf-8", errors="replace")[-8000:]
        excerpt = sanitize_error_message(raw_output, fallback="(no output)")
        passed = exit_code == 0 and not timed_out
        if timed_out:
            summary = f"{payload.runner} timed out after {payload.timeout_seconds}s"
        elif passed:
            summary = f"{payload.runner} passed (exit code 0) in {duration_ms}ms"
        else:
            summary = f"{payload.runner} failed (exit code {exit_code}) in {duration_ms}ms"

        executed_at = utc_now()
        await self._repo.add_event(
            workspace_id=ws.id,
            actor_id=auth_ctx.user.id,
            event_type="TEST_RUN_PASSED" if passed else "TEST_RUN_FAILED",
            previous_status=ws.status.value,
            new_status=ws.status.value,
            details={
                "runner": payload.runner,
                "exit_code": exit_code,
                "passed": passed,
                "duration_ms": duration_ms,
                "summary": summary,
            },
            now=executed_at,
        )
        ws.updated_at = executed_at
        await self._session.commit()
        self._metrics.increment("workspace_test_runs")
        return WorkspaceTestRunResponse(
            workspace_id=ws.id,
            project_id=ws.project_id,
            runner=payload.runner,
            passed=passed,
            exit_code=exit_code,
            duration_ms=duration_ms,
            summary=summary,
            output_excerpt=excerpt,
            executed_at=executed_at,
        )

    async def destroy_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> WorkspaceResponse:
        ws = await self._require_authorized_workspace(
            workspace_id=workspace_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
            allow_admin_read=False,
        )
        WorkspaceStateService.assert_transition(ws.status, WorkspaceStatus.DESTROYED)
        if ws.container_id:
            await self._docker.remove_container(ws.container_id, force=True)
        self._extractor.remove_workspace_directory(Path(ws.workspace_path))

        now = utc_now()
        prev = ws.status.value
        ws.status = WorkspaceStatus.DESTROYED
        ws.destroyed_at = now
        ws.updated_at = now
        await self._repo.add_event(
            workspace_id=ws.id,
            actor_id=auth_ctx.user.id,
            event_type="WORKSPACE_DESTROYED",
            previous_status=prev,
            new_status=WorkspaceStatus.DESTROYED.value,
            now=now,
        )
        await self._session.commit()
        self._metrics.increment("workspaces_destroyed")
        return WorkspaceResponse.model_validate(ws)

    async def _expire_workspace(self, ws: Workspace, *, actor_id: uuid.UUID) -> None:
        if ws.container_id:
            await self._docker.remove_container(ws.container_id, force=True)
        self._extractor.remove_workspace_directory(Path(ws.workspace_path))
        now = utc_now()
        prev = ws.status.value
        ws.status = WorkspaceStatus.EXPIRED
        ws.destroyed_at = now
        ws.updated_at = now
        await self._repo.add_event(
            workspace_id=ws.id,
            actor_id=actor_id,
            event_type="WORKSPACE_EXPIRED",
            previous_status=prev,
            new_status=WorkspaceStatus.EXPIRED.value,
            now=now,
        )
        await self._session.commit()
        self._metrics.increment("workspaces_expired")

    async def cleanup_expired_workspaces(self, *, auth_ctx: AuthContext) -> WorkspaceCleanupResponse:
        if auth_ctx.user.role not in {UserRole.ADMIN, UserRole.OWNER}:
            raise ForbiddenError("Only ADMIN or OWNER accounts may trigger expired workspace cleanup.")
        now = utc_now()
        expired = await self._repo.list_expired_active(now=now)
        destroyed_ids: list[uuid.UUID] = []
        errors = 0
        for ws in expired:
            if auth_ctx.user.role == UserRole.OWNER and ws.owner_id != auth_ctx.user.id:
                continue
            try:
                await self._expire_workspace(ws, actor_id=auth_ctx.user.id)
                destroyed_ids.append(ws.id)
            except Exception:
                errors += 1
        return WorkspaceCleanupResponse(
            expired_count=len(destroyed_ids),
            destroyed_workspace_ids=destroyed_ids,
            errors=errors,
        )

