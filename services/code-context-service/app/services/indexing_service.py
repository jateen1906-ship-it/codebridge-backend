"""Versioned repository indexing, reindexing, failure safety, & status service."""

from __future__ import annotations

import time
import uuid
from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.github_client import GitHubServiceClient
from app.clients.project_client import ProjectClient, assert_project_access
from app.core.dependencies import AuthContext
from app.core.exceptions import (
    CodeContextServiceError,
    ConflictError,
    IndexingFailedError,
    NotFoundError,
)
from app.core.logging import MetricsRegistry, get_logger
from app.models.base import utc_now
from app.models.code_chunk import CodeChunk
from app.models.indexed_file import IndexedFile, IndexedFileStatus
from app.models.indexing_job import IndexingJob, IndexingJobStatus, IndexingJobType
from app.models.repository_index import IndexStatus, RepositoryIndex
from app.repositories.chunk_repository import CodeChunkRepository
from app.repositories.file_repository import IndexedFileRepository
from app.repositories.index_repository import RepositoryIndexRepository
from app.repositories.indexing_job_repository import IndexingJobRepository
from app.schemas.indexing import (
    IndexingActionResponse,
    IndexingJobSummary,
    IndexMetadataResponse,
    IndexStatusResponse,
)
from app.services.chunking_service import ChunkDraft, ChunkingService
from app.services.embedding_service import EmbeddingService
from app.services.repository_ingestion_service import IngestionResult, RepositoryIngestionService
from app.services.vector_store_service import VectorStoreService

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("services.indexing")


def _to_index_schema(row: RepositoryIndex) -> IndexMetadataResponse:
    return IndexMetadataResponse(
        id=row.id,
        project_id=row.project_id,
        github_repository_id=row.github_repository_id,
        repository_full_name=row.repository_full_name,
        branch=row.branch,
        commit_sha=row.commit_sha,
        index_version=row.index_version,
        embedding_provider=row.embedding_provider,
        embedding_model=row.embedding_model,
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        is_active=row.is_active,
        files_discovered=row.files_discovered,
        files_indexed=row.files_indexed,
        files_skipped=row.files_skipped,
        chunks_generated=row.chunks_generated,
        total_bytes_indexed=row.total_bytes_indexed,
        error_message=row.error_message,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_job_schema(row: IndexingJob) -> IndexingJobSummary:
    return IndexingJobSummary(
        id=row.id,
        project_id=row.project_id,
        repository_index_id=row.repository_index_id,
        job_type=row.job_type.value if hasattr(row.job_type, "value") else str(row.job_type),
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        branch=row.branch,
        started_at=row.started_at,
        completed_at=row.completed_at,
        files_processed=row.files_processed,
        files_skipped=row.files_skipped,
        chunks_generated=row.chunks_generated,
        error_code=row.error_code,
        error_message=row.error_message,
    )


class IndexingService:
    """Orchestrates versioned repository indexing and non-destructive reindexing."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        project_client: ProjectClient,
        github_client: GitHubServiceClient,
        embedding_service: EmbeddingService,
        vector_store_service: VectorStoreService,
        metrics: MetricsRegistry,
    ) -> None:
        self._session = session
        self._settings = settings
        self._project_client = project_client
        self._github_client = github_client
        self._embedding_service = embedding_service
        self._vector_store = vector_store_service
        self._metrics = metrics

        self._index_repo = RepositoryIndexRepository(session)
        self._job_repo = IndexingJobRepository(session)
        self._file_repo = IndexedFileRepository(session)
        self._chunk_repo = CodeChunkRepository(session)
        self._ingestion_service = RepositoryIngestionService(
            settings=settings,
            github_client=github_client,
        )
        self._chunking_service = ChunkingService(settings)

    async def start_index(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
        branch: str | None = None,
        is_reindex: bool = False,
    ) -> IndexingActionResponse:
        """Index (or reindex) a project repository atomically with previous active safety."""
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

        # Verify connected repository exists BEFORE claiming version numbers or jobs.
        repo_info = await self._github_client.get_project_repository_status(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )

        inflight = await self._job_repo.get_inflight_for_project(project_id)
        if inflight is not None:
            raise ConflictError(
                "An indexing job is already running for this project.",
                error_code="INDEXING_IN_PROGRESS",
            )

        existing_active = await self._index_repo.get_active_for_project(project_id)
        job_type = (
            IndexingJobType.REINDEX
            if (is_reindex or existing_active is not None)
            else IndexingJobType.INITIAL
        )

        started_at: datetime = utc_now()
        next_version = await self._index_repo.get_next_version(project_id)
        collection_name = self._vector_store.build_versioned_collection_name(
            project_id,
            next_version,
        )
        target_branch = branch or repo_info.default_branch

        new_index = RepositoryIndex(
            project_id=project_id,
            github_repository_id=repo_info.github_repository_id,
            repository_full_name=repo_info.full_name,
            branch=target_branch,
            index_version=next_version,
            collection_name=collection_name,
            embedding_provider=self._embedding_service.provider_name,
            embedding_model=self._embedding_service.model_name,
            status=IndexStatus.INDEXING,
            is_active=False,
            started_at=started_at,
        )
        new_job = IndexingJob(
            project_id=project_id,
            repository_index_id=new_index.id,
            triggered_by=auth_ctx.user.id,
            job_type=job_type,
            status=IndexingJobStatus.RUNNING,
            branch=target_branch,
            started_at=started_at,
        )

        try:
            new_index = await self._index_repo.create(new_index)
            new_job.repository_index_id = new_index.id
            new_job = await self._job_repo.create(new_job)
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(
                "An indexing job is already in progress for this project.",
                error_code="INDEXING_IN_PROGRESS",
            ) from exc

        self._metrics.increment("indexing_jobs_started")
        t0 = time.perf_counter()

        try:
            ingestion: IngestionResult = await self._ingestion_service.ingest_repository(
                project_id=project_id,
                bearer_token=auth_ctx.bearer_token,
                request_id=request_id,
                requested_branch=branch,
            )

            all_chunks: list[ChunkDraft] = []
            chunk_file_paths: list[str] = []
            for file_rec in ingestion.files:
                if file_rec.status == IndexedFileStatus.INDEXED and file_rec.content:
                    file_chunks = self._chunking_service.chunk_file(
                        file_path=file_rec.file_path,
                        language=file_rec.language or "text",
                        text=file_rec.content,
                    )
                    for ch in file_chunks:
                        all_chunks.append(ch)
                        chunk_file_paths.append(file_rec.file_path)

            chunk_counts_by_path = Counter(chunk_file_paths)

            # Generate embeddings and store vectors in the NEW versioned collection first.
            vector_ids: list[str] = []
            if all_chunks:
                embeddings = await self._embedding_service.embed_documents(
                    [chunk.content for chunk in all_chunks]
                )
                vector_ids = await self._vector_store.index_chunks(
                    collection_name=collection_name,
                    project_id=project_id,
                    repository_index_id=new_index.id,
                    index_version=next_version,
                    chunks=all_chunks,
                    embeddings=embeddings,
                )

            # Persist file & chunk metadata rows, then atomically flip active version.
            indexed_file_rows: list[IndexedFile] = []
            path_to_file_row_id: dict[str, uuid.UUID] = {}
            now = utc_now()
            for file_rec in ingestion.files:
                file_row = IndexedFile(
                    id=uuid.uuid4(),
                    repository_index_id=new_index.id,
                    project_id=project_id,
                    file_path=file_rec.file_path,
                    file_name=file_rec.file_name,
                    file_extension=file_rec.file_extension,
                    language=file_rec.language,
                    file_sha=file_rec.file_sha,
                    size_bytes=file_rec.size_bytes,
                    chunk_count=chunk_counts_by_path.get(file_rec.file_path, 0),
                    status=file_rec.status,
                    skip_reason=file_rec.skip_reason,
                    indexed_at=now,
                )
                indexed_file_rows.append(file_row)
                path_to_file_row_id[file_rec.file_path] = file_row.id

            await self._file_repo.bulk_create(indexed_file_rows)

            chunk_rows: list[CodeChunk] = []
            for idx, chunk in enumerate(all_chunks):
                chunk_rows.append(
                    CodeChunk(
                        id=uuid.uuid4(),
                        repository_index_id=new_index.id,
                        indexed_file_id=path_to_file_row_id[chunk.file_path],
                        project_id=project_id,
                        chunk_identifier=chunk.chunk_identifier,
                        file_path=chunk.file_path,
                        language=chunk.language,
                        chunk_index=chunk.chunk_index,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        token_estimate=chunk.token_estimate,
                        content_hash=chunk.content_hash,
                        vector_id=vector_ids[idx],
                        created_at=now,
                    )
                )
            await self._chunk_repo.bulk_create(chunk_rows)

            completed_at = utc_now()
            # Keep branch synced to actual resolved branch if default changed.
            new_index.branch = ingestion.branch
            activated = await self._index_repo.activate_version(
                project_id=project_id,
                index_id=new_index.id,
                commit_sha=ingestion.commit_sha,
                files_discovered=ingestion.files_discovered,
                files_indexed=ingestion.files_indexed,
                files_skipped=ingestion.files_skipped,
                chunks_generated=len(all_chunks),
                total_bytes_indexed=ingestion.total_bytes_indexed,
                completed_at=completed_at,
            )
            completed_job = await self._job_repo.mark_completed(
                job_id=new_job.id,
                completed_at=completed_at,
                files_processed=ingestion.files_indexed,
                files_skipped=ingestion.files_skipped,
                chunks_generated=len(all_chunks),
            )
            await self._session.commit()

            await self._cleanup_old_vector_collections(
                project_id=project_id,
                active_version=next_version,
            )

            duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            self._metrics.increment("indexing_jobs_completed")
            self._metrics.increment("indexing_duration_ms_total", duration_ms)
            self._metrics.increment("files_indexed_total", ingestion.files_indexed)
            self._metrics.increment("files_skipped_total", ingestion.files_skipped)
            self._metrics.increment("chunks_generated_total", len(all_chunks))

            logger.info(
                "Repository indexing completed",
                extra={
                    "event": "repository_indexed",
                    "project_id": str(project_id),
                    "job_id": str(new_job.id),
                    "index_version": next_version,
                    "files_indexed": ingestion.files_indexed,
                    "files_skipped": ingestion.files_skipped,
                    "chunks_generated": len(all_chunks),
                    "duration_ms": duration_ms,
                },
            )

            assert activated is not None and completed_job is not None
            return IndexingActionResponse(
                success=True,
                project_id=project_id,
                job=_to_job_schema(completed_job),
                index=_to_index_schema(activated),
                skipped_reasons=ingestion.skipped_reasons,
                request_id=request_id,
            )

        except CodeContextServiceError as exc:
            await self._fail_index_build(
                index_id=new_index.id,
                job_id=new_job.id,
                collection_name=collection_name,
                error_code=exc.error_code,
                error_message=exc.message,
                project_id=project_id,
                index_version=next_version,
            )
            raise
        except Exception as exc:
            await self._fail_index_build(
                index_id=new_index.id,
                job_id=new_job.id,
                collection_name=collection_name,
                error_code="INDEXING_FAILED",
                error_message="Unexpected repository indexing error.",
                project_id=project_id,
                index_version=next_version,
            )
            raise IndexingFailedError("Repository indexing failed unexpectedly.") from exc

    async def _fail_index_build(
        self,
        *,
        index_id: uuid.UUID,
        job_id: uuid.UUID,
        collection_name: str,
        error_code: str,
        error_message: str,
        project_id: uuid.UUID,
        index_version: int,
    ) -> None:
        """Roll back incomplete vectors & mark pending version FAILED without hurting active index."""
        await self._session.rollback()
        await self._vector_store.delete_collection(collection_name)

        now = utc_now()
        await self._index_repo.mark_failed(
            index_id=index_id,
            error_message=error_message,
            completed_at=now,
        )
        await self._job_repo.mark_failed(
            job_id=job_id,
            completed_at=now,
            error_code=error_code,
            error_message=error_message,
        )
        await self._session.commit()

        self._metrics.increment("indexing_jobs_failed")
        logger.warning(
            "Repository indexing build failed cleanly; previous active index preserved",
            extra={
                "event": "repository_indexing_failed",
                "project_id": str(project_id),
                "job_id": str(job_id),
                "index_version": index_version,
                "error_code": error_code,
            },
        )

    async def _cleanup_old_vector_collections(
        self,
        *,
        project_id: uuid.UUID,
        active_version: int,
    ) -> None:
        """Delete retired ChromaDB collections past retention while keeping DB insight history intact."""
        retention = self._settings.INDEX_RETENTION_VERSIONS
        all_indexes = await self._index_repo.list_for_project(project_id)
        ready_or_stale = [
            idx
            for idx in all_indexes
            if idx.status in {IndexStatus.READY, IndexStatus.STALE}
            and idx.index_version != active_version
        ]
        # Keep the active index plus up to (retention - 1) most recent previous collections.
        keep_count = max(0, retention - 1)
        to_prune = ready_or_stale[keep_count:]
        for old_idx in to_prune:
            await self._vector_store.delete_collection(old_idx.collection_name)

    async def get_status(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> IndexStatusResponse:
        """Return safe indexing status, counts, language stats, and skip summary."""
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

        active_index = await self._index_repo.get_active_for_project(project_id)
        latest_index = await self._index_repo.get_latest_for_project(project_id)
        latest_job = await self._job_repo.get_latest_for_project(project_id)

        if active_index is None and latest_index is None and latest_job is None:
            raise NotFoundError(
                "No repository index exists yet for this project.",
                error_code="INDEX_NOT_FOUND",
            )

        target_for_files = active_index or latest_index
        skipped_reasons: Counter[str] = Counter()
        indexed_languages: Counter[str] = Counter()
        if target_for_files is not None:
            files = await self._file_repo.list_by_index_id(target_for_files.id)
            for file_row in files:
                if file_row.status == IndexedFileStatus.SKIPPED and file_row.skip_reason:
                    skipped_counter_key = file_row.skip_reason
                    skipped_reasons[skipped_counter_key] += 1
                elif file_row.status == IndexedFileStatus.INDEXED:
                    lang = file_row.language or "text"
                    indexed_languages[lang] += 1

        if latest_job is not None and latest_job.status in {
            IndexingJobStatus.PENDING,
            IndexingJobStatus.RUNNING,
        }:
            overall_status = "INDEXING"
        elif active_index is not None:
            overall_status = active_index.status.value
        elif latest_index is not None:
            overall_status = latest_index.status.value
        else:
            overall_status = "NOT_INDEXED"

        return IndexStatusResponse(
            project_id=project_id,
            has_active_index=active_index is not None,
            index_status=overall_status,
            active_index=_to_index_schema(active_index) if active_index else None,
            latest_index=_to_index_schema(latest_index) if latest_index else None,
            latest_job=_to_job_schema(latest_job) if latest_job else None,
            skipped_reasons=dict(skipped_reasons),
            indexed_languages=dict(indexed_languages),
        )

