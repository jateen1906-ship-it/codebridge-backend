"""Bounded, deterministic repository ingestion exclusively via GitHub Service."""

from __future__ import annotations

import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field

from app.clients.github_client import ConnectedRepositoryInfo, GitHubServiceClient
from app.core.config import Settings
from app.core.exceptions import (
    BadRequestError,
    IndexLimitExceededError,
)
from app.core.security import validate_safe_repo_path
from app.models.indexed_file import IndexedFileStatus
from app.services.file_filter_service import FileFilterService, is_excluded_directory
from app.services.file_parser_service import FileParserService


@dataclass(frozen=True, slots=True)
class IngestedFileRecord:
    """Normalized outcome for a single discovered repository file."""

    file_path: str
    file_name: str
    file_extension: str | None
    language: str | None
    file_sha: str | None
    size_bytes: int
    status: IndexedFileStatus
    skip_reason: str | None = None
    content: str | None = None


@dataclass(slots=True)
class IngestionResult:
    """Complete bounded ingestion snapshot ready for chunking and vector indexing."""

    repository: ConnectedRepositoryInfo
    branch: str
    commit_sha: str | None
    files: list[IngestedFileRecord] = field(default_factory=list)
    files_discovered: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    total_bytes_indexed: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)


class RepositoryIngestionService:
    """Traverse connected repository contents strictly through GitHub Service with safety caps."""

    def __init__(
        self,
        *,
        settings: Settings,
        github_client: GitHubServiceClient,
    ) -> None:
        self._settings = settings
        self._github_client = github_client
        self._file_filter = FileFilterService(settings)
        self._file_parser = FileParserService(settings)

    async def _resolve_branch_and_sha(
        self,
        *,
        project_id: uuid.UUID,
        repo: ConnectedRepositoryInfo,
        requested_branch: str | None,
        bearer_token: str,
        request_id: str,
    ) -> tuple[str, str | None]:
        target_branch = requested_branch or repo.default_branch
        branches = []
        for page in range(1, 6):
            page_items = await self._github_client.list_branches(
                project_id,
                bearer_token=bearer_token,
                request_id=request_id,
                page=page,
                per_page=100,
            )
            branches.extend(page_items)
            if len(page_items) < 100:
                break

        for branch_item in branches:
            if branch_item.name == target_branch:
                return target_branch, branch_item.commit_sha

        if requested_branch is not None and branches:
            raise BadRequestError(
                f"Branch '{requested_branch}' was not found in repository '{repo.full_name}'.",
                error_code="VALIDATION_ERROR",
            )
        return target_branch, None

    async def ingest_repository(
        self,
        *,
        project_id: uuid.UUID,
        bearer_token: str,
        request_id: str,
        requested_branch: str | None = None,
    ) -> IngestionResult:
        """Fetch repository metadata, traverse directories safely, and parse eligible source files."""
        start_monotonic = time.monotonic()

        def _check_deadline() -> None:
            elapsed = time.monotonic() - start_monotonic
            if elapsed > self._settings.MAX_INDEX_DURATION_SECONDS:
                raise IndexLimitExceededError(
                    f"Repository indexing exceeded maximum duration limit ({self._settings.MAX_INDEX_DURATION_SECONDS}s)."
                )

        repo_info = await self._github_client.get_project_repository_status(
            project_id,
            bearer_token=bearer_token,
            request_id=request_id,
        )
        branch, commit_sha = await self._resolve_branch_and_sha(
            project_id=project_id,
            repo=repo_info,
            requested_branch=requested_branch,
            bearer_token=bearer_token,
            request_id=request_id,
        )

        queue: deque[tuple[str, int]] = deque([("", 0)])
        visited_dirs: set[str] = set()
        seen_file_paths: set[str] = set()
        records: list[IngestedFileRecord] = []
        skipped_counter: Counter[str] = Counter()

        dir_traversals = 0
        files_discovered = 0
        files_indexed = 0
        files_skipped = 0
        total_bytes_indexed = 0

        while queue:
            _check_deadline()
            dir_path, depth = queue.popleft()
            normalized_dir = validate_safe_repo_path(dir_path)
            if normalized_dir in visited_dirs:
                continue
            visited_dirs.add(normalized_dir)

            if depth > self._settings.MAX_DIRECTORY_DEPTH:
                raise IndexLimitExceededError(
                    f"Repository directory depth exceeded limit ({self._settings.MAX_DIRECTORY_DEPTH})."
                )

            if normalized_dir and is_excluded_directory(normalized_dir):
                continue

            dir_traversals += 1
            if dir_traversals > self._settings.MAX_DIRECTORY_TRAVERSALS:
                raise IndexLimitExceededError(
                    f"Repository exceeded directory traversal limit ({self._settings.MAX_DIRECTORY_TRAVERSALS})."
                )

            node = await self._github_client.get_contents(
                project_id,
                bearer_token=bearer_token,
                request_id=request_id,
                path=normalized_dir,
                ref=branch,
            )

            if node.kind == "file":
                # Single-file edge case if root returned file unexpectedly
                entries_to_handle = []
            else:
                entries_to_handle = sorted(node.entries, key=lambda e: (e.entry_type, e.path))

            for entry in entries_to_handle:
                _check_deadline()
                safe_entry_path = validate_safe_repo_path(entry.path)
                if entry.entry_type == "dir":
                    if not is_excluded_directory(safe_entry_path):
                        next_depth = depth + 1
                        if next_depth > self._settings.MAX_DIRECTORY_DEPTH:
                            raise IndexLimitExceededError(
                                f"Repository directory depth exceeded limit ({self._settings.MAX_DIRECTORY_DEPTH})."
                            )
                        queue.append((safe_entry_path, next_depth))
                    continue

                if entry.entry_type in {"symlink", "submodule", "other"}:
                    if safe_entry_path in seen_file_paths:
                        continue
                    seen_file_paths.add(safe_entry_path)
                    files_discovered += 1
                    files_skipped += 1
                    skipped_counter["unsupported_entry_type"] += 1
                    records.append(
                        IngestedFileRecord(
                            file_path=safe_entry_path,
                            file_name=entry.name,
                            file_extension=None,
                            language=None,
                            file_sha=entry.sha,
                            size_bytes=max(0, int(entry.size)),
                            status=IndexedFileStatus.SKIPPED,
                            skip_reason="unsupported_entry_type",
                        )
                    )
                    continue

                if entry.entry_type != "file":
                    continue

                if safe_entry_path in seen_file_paths:
                    continue
                seen_file_paths.add(safe_entry_path)
                files_discovered += 1

                decision = self._file_filter.evaluate_file(
                    safe_entry_path,
                    declared_size=entry.size,
                )
                if not decision.allowed:
                    reason = decision.skip_reason or "filtered_out"
                    files_skipped += 1
                    skipped_counter[reason] += 1
                    records.append(
                        IngestedFileRecord(
                            file_path=decision.normalized_path,
                            file_name=decision.file_name,
                            file_extension=decision.extension or None,
                            language=decision.language,
                            file_sha=entry.sha,
                            size_bytes=max(0, int(entry.size)),
                            status=IndexedFileStatus.SKIPPED,
                            skip_reason=reason,
                        )
                    )
                    continue

                if files_indexed >= self._settings.MAX_FILES_PER_INDEX:
                    raise IndexLimitExceededError(
                        f"Repository exceeded maximum indexed file count ({self._settings.MAX_FILES_PER_INDEX})."
                    )

                file_node = await self._github_client.get_contents(
                    project_id,
                    bearer_token=bearer_token,
                    request_id=request_id,
                    path=decision.normalized_path,
                    ref=branch,
                )
                if file_node.kind != "file":
                    files_skipped += 1
                    skipped_counter["unexpected_node_kind"] += 1
                    continue

                parsed = self._file_parser.parse(file_node)
                if not parsed.accepted or parsed.text is None:
                    reason = parsed.skip_reason or "parse_skipped"
                    files_skipped += 1
                    skipped_counter[reason] += 1
                    records.append(
                        IngestedFileRecord(
                            file_path=decision.normalized_path,
                            file_name=decision.file_name,
                            file_extension=decision.extension or None,
                            language=decision.language,
                            file_sha=file_node.sha or entry.sha,
                            size_bytes=parsed.size_bytes or max(0, int(entry.size)),
                            status=IndexedFileStatus.SKIPPED,
                            skip_reason=reason,
                        )
                    )
                    continue

                projected_total = total_bytes_indexed + parsed.size_bytes
                if projected_total > self._settings.MAX_TOTAL_REPOSITORY_BYTES:
                    raise IndexLimitExceededError(
                        f"Repository exceeded total indexed size cap ({self._settings.MAX_TOTAL_REPOSITORY_BYTES} bytes)."
                    )

                total_bytes_indexed = projected_total
                files_indexed += 1
                records.append(
                    IngestedFileRecord(
                        file_path=decision.normalized_path,
                        file_name=decision.file_name,
                        file_extension=decision.extension or None,
                        language=decision.language,
                        file_sha=file_node.sha or entry.sha,
                        size_bytes=parsed.size_bytes,
                        status=IndexedFileStatus.INDEXED,
                        skip_reason=None,
                        content=parsed.text,
                    )
                )

        return IngestionResult(
            repository=repo_info,
            branch=branch,
            commit_sha=commit_sha,
            files=records,
            files_discovered=files_discovered,
            files_indexed=files_indexed,
            files_skipped=files_skipped,
            total_bytes_indexed=total_bytes_indexed,
            skipped_reasons=dict(skipped_counter),
        )

