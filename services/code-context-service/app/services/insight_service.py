"""Deterministic repository health checks & optional labeled AI architectural insights."""

from __future__ import annotations

import json
import posixpath
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.project_client import ProjectClient, assert_project_access
from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, UpstreamServiceUnavailableError
from app.core.logging import MetricsRegistry, get_logger
from app.models.base import utc_now
from app.models.indexed_file import IndexedFile, IndexedFileStatus
from app.models.repository_index import IndexStatus, RepositoryIndex
from app.repositories.chunk_repository import CodeChunkRepository
from app.repositories.file_repository import IndexedFileRepository
from app.repositories.index_repository import RepositoryIndexRepository
from app.repositories.insight_repository import RepositoryInsightRepository
from app.models.repository_insight import RepositoryInsight
from app.schemas.insights import (
    AIInsightSummary,
    DeterministicInsightsReport,
    InsightsEnvelopeResponse,
    RepositoryInsightResponse,
)
from app.services.rag_service import LLMClient, parse_llm_json_object

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("services.insights")

_MANIFEST_FILENAMES = frozenset(
    {
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "package.json",
        "go.mod",
        "cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gemfile",
        "composer.json",
    }
)

_LINT_FORMAT_FILENAMES = frozenset(
    {
        "ruff.toml",
        ".ruff.toml",
        ".flake8",
        ".pylintrc",
        "mypy.ini",
        ".mypy.ini",
        "tsconfig.json",
        ".golangci.yml",
        ".golangci.yaml",
        ".prettierrc",
        ".prettierrc.json",
        ".prettierrc.yml",
        ".eslintrc",
        ".eslintrc.json",
        ".eslintrc.yml",
        "eslint.config.js",
        "eslint.config.mjs",
    }
)

_CONTAINER_CI_FILENAMES = frozenset(
    {
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        ".gitlab-ci.yml",
        "jenkinsfile",
    }
)

_SYSTEM_INSIGHT_PROMPT = (
    "You are a software architecture reviewer generating a concise repository insight summary.\n"
    "RULES:\n"
    "1. Analyze ONLY the verified deterministic facts and file list inside <repository_facts>.\n"
    "2. Never claim tests were executed or passed. Only mention static test file presence.\n"
    "3. Output strictly valid JSON matching keys: architecture_overview (string), "
    "likely_entry_points (list[string]), missing_setup_or_hygiene_signals (list[string]), "
    "high_risk_or_incomplete_areas (list[string]), evidence_files (list[string]).\n"
    "4. Only cite file paths that appear inside <repository_facts>."
)


def _is_test_path(file_path: str, file_name: str) -> bool:
    lower_path = file_path.lower()
    lower_name = file_name.lower()
    segments = lower_path.split("/")
    if any(seg in {"tests", "test", "__tests__", "spec", "specs"} for seg in segments[:-1]):
        return True
    if lower_name.startswith("test_") or lower_name.endswith(
        ("_test.py", "_test.go", ".test.js", ".test.ts", ".test.tsx", ".spec.js", ".spec.ts")
    ):
        return True
    return False


def _to_insight_response(row: RepositoryInsight) -> RepositoryInsightResponse:
    det = DeterministicInsightsReport.model_validate(row.deterministic_report)
    ai_summary = AIInsightSummary.model_validate(row.ai_summary) if row.ai_summary else None
    return RepositoryInsightResponse(
        id=row.id,
        project_id=row.project_id,
        repository_index_id=row.repository_index_id,
        index_version=row.index_version,
        ai_generated=row.ai_generated,
        llm_provider=row.llm_provider,
        llm_model=row.llm_model,
        generated_at=row.generated_at,
        deterministic_report=det,
        ai_summary=ai_summary,
    )


class InsightService:
    """Computes deterministic repository diagnostics & optional labeled AI summaries."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        project_client: ProjectClient,
        llm_client: LLMClient,
        metrics: MetricsRegistry,
    ) -> None:
        self._session = session
        self._settings = settings
        self._project_client = project_client
        self._llm_client = llm_client
        self._metrics = metrics

        self._index_repo = RepositoryIndexRepository(session)
        self._file_repo = IndexedFileRepository(session)
        self._chunk_repo = CodeChunkRepository(session)
        self._insight_repo = RepositoryInsightRepository(session)

    def _build_deterministic_report(
        self,
        *,
        active_index: RepositoryIndex,
        latest_index: RepositoryIndex | None,
        files: list[IndexedFile],
        total_chunks: int,
    ) -> DeterministicInsightsReport:
        languages_counter: Counter[str] = Counter()
        skipped_counter: Counter[str] = Counter()
        readme_paths: list[str] = []
        test_file_paths: list[str] = []
        dependency_manifests: list[str] = []
        container_and_ci_configs: list[str] = []
        lint_and_format_configs: list[str] = []
        large_files: list[dict[str, Any]] = []

        large_threshold = min(32768, max(8192, self._settings.MAX_FILE_BYTES // 2))

        for row in files:
            lower_path = row.file_path.lower()
            lower_name = row.file_name.lower()

            if row.status == IndexedFileStatus.INDEXED:
                languages_counter[row.language or "text"] += 1
                if row.size_bytes >= large_threshold:
                    large_files.append(
                        {"file_path": row.file_path, "size_bytes": int(row.size_bytes)}
                    )
            elif row.status == IndexedFileStatus.SKIPPED and row.skip_reason:
                skipped_counter[row.skip_reason] += 1

            # Inspect discovered paths (indexed or extension-skipped like Dockerfile) for repo signals.
            if lower_name.startswith("readme"):
                readme_paths.append(row.file_path)
            if _is_test_path(row.file_path, row.file_name):
                test_file_paths.append(row.file_path)
            if lower_name in _MANIFEST_FILENAMES or lower_name.endswith(".csproj"):
                dependency_manifests.append(row.file_path)
            if lower_name in _CONTAINER_CI_FILENAMES or lower_path.startswith(".github/workflows/"):
                container_and_ci_configs.append(row.file_path)
            if lower_name in _LINT_FORMAT_FILENAMES or lower_name.startswith(".eslintrc"):
                lint_and_format_configs.append(row.file_path)

        large_files.sort(key=lambda item: int(item["size_bytes"]), reverse=True)

        now = datetime.now(timezone.utc)
        completed_at = active_index.completed_at
        if completed_at is not None and completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        is_stale = (
            active_index.status != IndexStatus.READY
            or (latest_index is not None and latest_index.id != active_index.id)
            or (completed_at is not None and (now - completed_at) > timedelta(days=7))
        )

        warnings: list[str] = []
        if active_index.files_indexed == 0:
            warnings.append("No eligible source code files were indexed from this repository.")
        if not readme_paths:
            warnings.append("No README file detected in the repository root or docs.")
        if not test_file_paths:
            warnings.append("No test files or test directories detected in repository paths.")
        if not dependency_manifests:
            warnings.append("No standard package or dependency manifest detected.")
        if skipped_counter.get("secret_detected", 0) > 0 or skipped_counter.get("sensitive_file", 0) > 0:
            warnings.append(
                "One or more sensitive or secret-bearing files were excluded during indexing."
            )
        if is_stale:
            warnings.append("The active index may be stale; consider running a reindex.")

        return DeterministicInsightsReport(
            repository_full_name=active_index.repository_full_name,
            branch=active_index.branch,
            index_version=active_index.index_version,
            languages_detected=dict(sorted(languages_counter.items())),
            total_files_discovered=active_index.files_discovered,
            indexed_file_count=active_index.files_indexed,
            skipped_file_count=active_index.files_skipped,
            skipped_reasons=dict(sorted(skipped_counter.items())),
            total_indexed_bytes=active_index.total_bytes_indexed,
            total_chunks=total_chunks,
            has_readme=bool(readme_paths),
            readme_paths=sorted(readme_paths)[:10],
            has_tests=bool(test_file_paths),
            test_file_paths=sorted(test_file_paths)[:25],
            test_execution_note=(
                "Static presence indicator only: Code Context Service does not execute tests "
                "or verify runtime pass/fail outcomes."
            ),
            dependency_manifests=sorted(dependency_manifests)[:15],
            container_and_ci_configs=sorted(container_and_ci_configs)[:15],
            lint_and_format_configs=sorted(lint_and_format_configs)[:15],
            large_files=large_files[:10],
            last_indexed_at=active_index.completed_at,
            is_stale=is_stale,
            warnings=warnings,
        )

    async def _generate_ai_summary(
        self,
        *,
        report: DeterministicInsightsReport,
        indexed_paths: list[str],
    ) -> tuple[AIInsightSummary | None, bool, str | None, str | None]:
        if not self._llm_client.is_enabled:
            return None, False, None, None

        allowed_paths = set(indexed_paths)
        facts_payload = {
            "repository": report.repository_full_name,
            "branch": report.branch,
            "languages": report.languages_detected,
            "indexed_file_count": report.indexed_file_count,
            "has_readme": report.has_readme,
            "has_tests": report.has_tests,
            "dependency_manifests": report.dependency_manifests,
            "container_and_ci_configs": report.container_and_ci_configs,
            "warnings": report.warnings,
            "indexed_paths_sample": indexed_paths[:60],
        }
        user_prompt = (
            "<repository_facts>\n"
            f"{json.dumps(facts_payload, indent=2)}\n"
            "</repository_facts>"
        )

        if self._llm_client.provider_name == "mock":
            # Deterministic offline AI insight summary derived strictly from indexed paths & facts.
            entry_candidates = [
                p
                for p in indexed_paths
                if posixpath.basename(p).lower() in {"main.py", "app.py", "index.ts", "index.js", "server.py"}
            ][:5]
            summary = AIInsightSummary(
                architecture_overview=(
                    f"Repository {report.repository_full_name} ({report.branch}) contains "
                    f"{report.indexed_file_count} indexed source file(s) across "
                    f"{', '.join(report.languages_detected.keys()) or 'unknown languages'}."
                ),
                likely_entry_points=entry_candidates,
                missing_setup_or_hygiene_signals=list(report.warnings),
                high_risk_or_incomplete_areas=(
                    ["Missing automated test suite presence"] if not report.has_tests else []
                ),
                evidence_files=indexed_paths[:8],
            )
            return summary, True, "mock", self._llm_client.model_name

        try:
            raw_text = await self._llm_client.generate_completion(
                system_prompt=_SYSTEM_INSIGHT_PROMPT,
                user_prompt=user_prompt,
            )
        except UpstreamServiceUnavailableError:
            if not self._llm_client.fallback_on_error:
                raise
            return None, False, None, None

        parsed = parse_llm_json_object(raw_text)
        if parsed is None:
            return None, False, None, None

        def _filter_paths(raw_list: object) -> list[str]:
            if not isinstance(raw_list, list):
                return []
            return [str(item) for item in raw_list if isinstance(item, str) and item in allowed_paths]

        def _clean_strs(raw_list: object) -> list[str]:
            if not isinstance(raw_list, list):
                return []
            return [str(item).strip() for item in raw_list if isinstance(item, str) and item.strip()][:10]

        overview = parsed.get("architecture_overview")
        if not isinstance(overview, str) or not overview.strip():
            return None, False, None, None

        summary = AIInsightSummary(
            architecture_overview=overview.strip()[:1200],
            likely_entry_points=_filter_paths(parsed.get("likely_entry_points")),
            missing_setup_or_hygiene_signals=_clean_strs(
                parsed.get("missing_setup_or_hygiene_signals")
            ),
            high_risk_or_incomplete_areas=_clean_strs(
                parsed.get("high_risk_or_incomplete_areas")
            ),
            evidence_files=_filter_paths(parsed.get("evidence_files")),
        )
        return (
            summary,
            True,
            self._llm_client.provider_name,
            self._llm_client.model_name,
        )

    async def generate_insights(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
        include_ai_summary: bool = True,
    ) -> InsightsEnvelopeResponse:
        """Compute, optionally summarize with AI, persist, and return repository insights (OWNER)."""
        project = await self._project_client.get_project(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        assert_project_access(
            project,
            auth_ctx.user,
            allow_assigned_freelancer=False,
            allow_admin=False,
        )

        active_index = await self._index_repo.get_active_for_project(project_id)
        if active_index is None:
            raise NotFoundError(
                "No active repository index exists for this project. Trigger indexing first.",
                error_code="INDEX_NOT_FOUND",
            )

        latest_index = await self._index_repo.get_latest_for_project(project_id)
        files = await self._file_repo.list_by_index_id(active_index.id)
        total_chunks = await self._chunk_repo.count_by_index_id(active_index.id)

        report = self._build_deterministic_report(
            active_index=active_index,
            latest_index=latest_index,
            files=files,
            total_chunks=total_chunks,
        )
        indexed_paths = [
            f.file_path for f in files if f.status == IndexedFileStatus.INDEXED
        ]

        ai_summary: AIInsightSummary | None = None
        ai_generated = False
        llm_provider: str | None = None
        llm_model: str | None = None
        if include_ai_summary:
            ai_summary, ai_generated, llm_provider, llm_model = await self._generate_ai_summary(
                report=report,
                indexed_paths=indexed_paths,
            )

        now = utc_now()
        insight_row = RepositoryInsight(
            id=uuid.uuid4(),
            project_id=project_id,
            repository_index_id=active_index.id,
            index_version=active_index.index_version,
            deterministic_report=report.model_dump(mode="json"),
            ai_summary=ai_summary.model_dump(mode="json") if ai_summary else None,
            ai_generated=ai_generated,
            llm_provider=llm_provider,
            llm_model=llm_model,
            generated_by=auth_ctx.user.id,
            generated_at=now,
            created_at=now,
        )
        created = await self._insight_repo.create(insight_row)
        await self._session.commit()

        self._metrics.increment("insights_generated")
        logger.info(
            "Repository insights generated",
            extra={
                "event": "repository_insights_generated",
                "project_id": str(project_id),
                "index_version": active_index.index_version,
            },
        )
        return InsightsEnvelopeResponse(
            success=True,
            data=_to_insight_response(created),
            request_id=request_id,
        )

    async def get_latest_insights(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> InsightsEnvelopeResponse:
        """Fetch latest persisted repository insights (OWNER, assigned FREELANCER, ADMIN)."""
        project = await self._project_client.get_project(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        assert_project_access(
            project,
            auth_ctx.user,
            allow_assigned_freelancer=True,
            allow_admin=True,
        )

        latest = await self._insight_repo.get_latest_for_project(project_id)
        if latest is None:
            raise NotFoundError(
                "No repository insights have been generated for this project yet.",
                error_code="INDEX_NOT_FOUND",
            )

        return InsightsEnvelopeResponse(
            success=True,
            data=_to_insight_response(latest),
            request_id=request_id,
        )

