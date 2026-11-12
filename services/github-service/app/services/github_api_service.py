"""Decrypted on-demand GitHub REST orchestration and safe response shaping."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Any, Literal

from app.clients.github_client import GitHubHTTPClient
from app.core.config import Settings
from app.core.encryption import TokenEncryptionError, TokenEncryptionService
from app.core.exceptions import GitHubUpstreamError, NotFoundError
from app.models.github_connection import GitHubConnection, GitHubConnectionStatus
from app.schemas.project_github import (
    BranchListResponse,
    BranchSummary,
    CommitListResponse,
    CommitSummary,
    RepositoryContentEntry,
    RepositoryContentsResponse,
)
from app.schemas.repository import (
    GitHubRepositoryListResponse,
    GitHubRepositoryPermissions,
    GitHubRepositorySummary,
)

_ALLOWED_ENTRY_TYPES: frozenset[str] = frozenset(
    {"file", "dir", "symlink", "submodule"}
)


def _parse_optional_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _normalize_entry_type(
    raw_type: object,
) -> Literal["file", "dir", "symlink", "submodule", "other"]:
    if isinstance(raw_type, str) and raw_type in _ALLOWED_ENTRY_TYPES:
        return raw_type  # type: ignore[return-value]
    return "other"


class GitHubAPIService:
    """Service coordinating decrypted OAuth credentials and safe GitHub REST response shaping."""

    def __init__(
        self,
        *,
        settings: Settings,
        encryption_service: TokenEncryptionService,
        github_client: GitHubHTTPClient,
    ) -> None:
        self._settings = settings
        self._encryption_service = encryption_service
        self._github_client = github_client

    def _decrypt_active_token(self, connection: GitHubConnection) -> str:
        if connection.status != GitHubConnectionStatus.ACTIVE:
            raise NotFoundError(
                "No active GitHub OAuth connection was found.",
                error_code="GITHUB_CONNECTION_NOT_FOUND",
            )
        try:
            return self._encryption_service.decrypt(
                connection.encrypted_access_token,
                owner_id=connection.owner_id,
            )
        except TokenEncryptionError as exc:
            raise GitHubUpstreamError(
                "Stored GitHub OAuth credential is unavailable or corrupted. Reconnect GitHub.",
                error_code="GITHUB_TOKEN_INVALID",
            ) from exc

    @staticmethod
    def shape_repository(raw: dict[str, Any]) -> GitHubRepositorySummary:
        """Transform a GitHub repository JSON object into a validated safe summary."""
        repo_id = raw.get("id")
        name = raw.get("name")
        full_name = raw.get("full_name")
        if (
            not isinstance(repo_id, int)
            or isinstance(repo_id, bool)
            or repo_id < 1
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(full_name, str)
            or "/" not in full_name
        ):
            raise GitHubUpstreamError("GitHub repository payload was incomplete.")

        owner_obj = raw.get("owner")
        owner_login = (
            str(owner_obj.get("login"))
            if isinstance(owner_obj, dict) and isinstance(owner_obj.get("login"), str)
            else full_name.split("/", 1)[0]
        )

        raw_description = raw.get("description")
        description = (
            raw_description[:1000]
            if isinstance(raw_description, str) and raw_description.strip()
            else None
        )

        raw_html_url = raw.get("html_url")
        html_url = (
            raw_html_url.strip()
            if isinstance(raw_html_url, str)
            and raw_html_url.strip().startswith(("https://", "http://"))
            else f"https://github.com/{full_name.strip()}"
        )

        raw_perms = raw.get("permissions")
        if isinstance(raw_perms, dict):
            permissions = GitHubRepositoryPermissions(
                admin=bool(raw_perms.get("admin", False)),
                maintain=bool(raw_perms.get("maintain", False)),
                push=bool(raw_perms.get("push", False)),
                pull=bool(raw_perms.get("pull", True)),
            )
        else:
            permissions = GitHubRepositoryPermissions()

        default_branch = (
            str(raw.get("default_branch")).strip()
            if isinstance(raw.get("default_branch"), str) and str(raw.get("default_branch")).strip()
            else "main"
        )

        return GitHubRepositorySummary(
            github_repository_id=repo_id,
            name=name.strip(),
            full_name=full_name.strip(),
            owner_login=owner_login.strip(),
            is_private=bool(raw.get("private", False)),
            default_branch=default_branch,
            description=description,
            html_url=html_url,
            is_archived=bool(raw.get("archived", False)),
            is_fork=bool(raw.get("fork", False)),
            permissions=permissions,
        )

    async def list_owner_repositories(
        self,
        *,
        connection: GitHubConnection,
        page: int,
        per_page: int,
        visibility: str,
    ) -> GitHubRepositoryListResponse:
        """Fetch paginated accessible repositories using decrypted on-demand token."""
        access_token = self._decrypt_active_token(connection)
        raw_items, has_next_page = await self._github_client.list_repositories(
            access_token=access_token,
            page=page,
            per_page=per_page,
            visibility=visibility,
        )
        items = [self.shape_repository(item) for item in raw_items]
        return GitHubRepositoryListResponse(
            items=items,
            page=page,
            per_page=per_page,
            has_next_page=has_next_page,
        )

    async def verify_repository(
        self,
        *,
        connection: GitHubConnection,
        full_name: str,
    ) -> GitHubRepositorySummary:
        """Verify live access and authoritative metadata for a single GitHub repository."""
        access_token = self._decrypt_active_token(connection)
        raw_repo = await self._github_client.get_repository(
            access_token=access_token,
            full_name=full_name,
        )
        return self.shape_repository(raw_repo)

    async def list_branches(
        self,
        *,
        project_id: uuid.UUID,
        connection: GitHubConnection,
        full_name: str,
        page: int,
        per_page: int,
    ) -> BranchListResponse:
        """Fetch paginated read-only branch summaries for a connected repository."""
        access_token = self._decrypt_active_token(connection)
        raw_branches, has_next_page = await self._github_client.list_branches(
            access_token=access_token,
            full_name=full_name,
            page=page,
            per_page=per_page,
        )
        items: list[BranchSummary] = []
        for raw_branch in raw_branches:
            name = raw_branch.get("name")
            commit_obj = raw_branch.get("commit")
            sha = commit_obj.get("sha") if isinstance(commit_obj, dict) else None
            if not isinstance(name, str) or not isinstance(sha, str):
                continue
            items.append(
                BranchSummary(
                    name=name,
                    commit_sha=sha,
                    protected=bool(raw_branch.get("protected", False)),
                )
            )
        return BranchListResponse(
            project_id=project_id,
            full_name=full_name,
            items=items,
            page=page,
            per_page=per_page,
            has_next_page=has_next_page,
        )

    async def get_contents(
        self,
        *,
        project_id: uuid.UUID,
        connection: GitHubConnection,
        full_name: str,
        repo_path: str,
        ref: str | None,
    ) -> RepositoryContentsResponse:
        """Fetch bounded read-only file content or directory entries from GitHub."""
        access_token = self._decrypt_active_token(connection)
        raw_data = await self._github_client.get_contents(
            access_token=access_token,
            full_name=full_name,
            repo_path=repo_path,
            ref=ref,
        )

        if isinstance(raw_data, list):
            max_entries = self._settings.GITHUB_CONTENTS_MAX_ENTRIES
            truncated = len(raw_data) > max_entries
            entries: list[RepositoryContentEntry] = []
            for raw_entry in raw_data[:max_entries]:
                if not isinstance(raw_entry, dict):
                    continue
                entry_name = raw_entry.get("name")
                entry_path = raw_entry.get("path")
                entry_sha = raw_entry.get("sha")
                if (
                    not isinstance(entry_name, str)
                    or not isinstance(entry_path, str)
                    or not isinstance(entry_sha, str)
                ):
                    continue
                raw_size = raw_entry.get("size")
                entry_size = int(raw_size) if isinstance(raw_size, int) and not isinstance(raw_size, bool) else 0
                entries.append(
                    RepositoryContentEntry(
                        name=entry_name,
                        path=entry_path,
                        type=_normalize_entry_type(raw_entry.get("type")),
                        size=max(0, entry_size),
                        sha=entry_sha,
                    )
                )
            return RepositoryContentsResponse(
                project_id=project_id,
                full_name=full_name,
                ref=ref,
                path=repo_path,
                kind="dir",
                truncated=truncated,
                entries=entries,
            )

        # Single file / content object
        max_bytes = self._settings.GITHUB_CONTENTS_MAX_BYTES
        raw_size_val = raw_data.get("size")
        reported_size = (
            int(raw_size_val)
            if isinstance(raw_size_val, int) and not isinstance(raw_size_val, bool)
            else 0
        )
        raw_sha = raw_data.get("sha")
        file_sha = raw_sha if isinstance(raw_sha, str) else None
        raw_encoding = raw_data.get("encoding")
        raw_content = raw_data.get("content")
        resolved_path = (
            str(raw_data.get("path"))
            if isinstance(raw_data.get("path"), str)
            else repo_path
        )

        is_binary = False
        truncated = reported_size > max_bytes
        decoded_text: str | None = None
        resolved_encoding: str | None = (
            str(raw_encoding) if isinstance(raw_encoding, str) else None
        )

        if raw_encoding == "base64" and isinstance(raw_content, str):
            compact_b64 = "".join(raw_content.splitlines())
            try:
                decoded_bytes = base64.b64decode(compact_b64, validate=False)
            except Exception as exc:
                raise GitHubUpstreamError("GitHub returned malformed base64 file content.") from exc

            if len(decoded_bytes) > max_bytes:
                truncated = True
                decoded_bytes = decoded_bytes[:max_bytes]

            if b"\x00" in decoded_bytes:
                is_binary = True
                resolved_encoding = "binary"
            else:
                try:
                    decoded_text = decoded_bytes.decode("utf-8")
                    resolved_encoding = "utf-8"
                except UnicodeDecodeError:
                    is_binary = True
                    resolved_encoding = "binary"
        elif isinstance(raw_content, str) and raw_encoding in {None, "utf-8"}:
            encoded = raw_content.encode("utf-8", errors="ignore")
            if len(encoded) > max_bytes:
                truncated = True
                decoded_text = encoded[:max_bytes].decode("utf-8", errors="ignore")
            else:
                decoded_text = raw_content
            resolved_encoding = "utf-8"

        return RepositoryContentsResponse(
            project_id=project_id,
            full_name=full_name,
            ref=ref,
            path=resolved_path,
            kind="file",
            sha=file_sha,
            size=max(0, reported_size),
            encoding=resolved_encoding,
            is_binary=is_binary,
            truncated=truncated,
            content=decoded_text,
        )

    async def list_commits(
        self,
        *,
        project_id: uuid.UUID,
        connection: GitHubConnection,
        full_name: str,
        page: int,
        per_page: int,
        sha: str | None,
        repo_path: str | None,
    ) -> CommitListResponse:
        """Fetch paginated read-only commit metadata for a connected repository."""
        access_token = self._decrypt_active_token(connection)
        raw_commits, has_next_page = await self._github_client.list_commits(
            access_token=access_token,
            full_name=full_name,
            page=page,
            per_page=per_page,
            sha=sha,
            repo_path=repo_path,
        )
        items: list[CommitSummary] = []
        for raw_item in raw_commits:
            commit_sha = raw_item.get("sha")
            if not isinstance(commit_sha, str) or not commit_sha.strip():
                continue
            commit_sha = commit_sha.strip()
            commit_obj = raw_item.get("commit") if isinstance(raw_item.get("commit"), dict) else {}
            message = str(commit_obj.get("message") or "").strip()[:500]

            commit_author = commit_obj.get("author") if isinstance(commit_obj.get("author"), dict) else {}
            author_name = (
                str(commit_author.get("name"))
                if isinstance(commit_author.get("name"), str)
                else None
            )
            committed_at = _parse_optional_iso_datetime(commit_author.get("date"))

            gh_author = raw_item.get("author") if isinstance(raw_item.get("author"), dict) else {}
            author_login = (
                str(gh_author.get("login"))
                if isinstance(gh_author.get("login"), str)
                else None
            )
            html_url = (
                str(raw_item.get("html_url"))
                if isinstance(raw_item.get("html_url"), str)
                and str(raw_item.get("html_url")).startswith(("https://", "http://"))
                else None
            )
            items.append(
                CommitSummary(
                    sha=commit_sha,
                    short_sha=commit_sha[:7],
                    message=message,
                    author_name=author_name,
                    author_login=author_login,
                    committed_at=committed_at,
                    html_url=html_url,
                )
            )
        return CommitListResponse(
            project_id=project_id,
            full_name=full_name,
            items=items,
            page=page,
            per_page=per_page,
            has_next_page=has_next_page,
        )

