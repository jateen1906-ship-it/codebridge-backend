"""Business logic for Observability Service health aggregation, secret scrubbing, and metrics."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.service_clients import ServiceProbeClient
from app.core.database import check_database_connectivity
from app.models.telemetry import TelemetryRecord
from app.schemas.observability import (
    ErrorLogListResponse,
    PlatformMetricsResponse,
    PlatformServicesStatusResponse,
    ServiceHealthSnapshot,
    ServiceMetricSummary,
    TelemetryCreateRequest,
    TelemetryRecordResponse,
)

_SECRET_PATTERNS = [
    re.compile(r"(?i)bearer\s+[a-z0-9._\-+/=]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{10,}"),
    re.compile(r"sk-[A-Za-z0-9\-_]{10,}"),
    re.compile(r"postgresql(?:\+\w+)?://[^\s\"']+"),
    re.compile(r"(?i)(?:password|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;\"']+"),
]


def scrub_text(raw: str | None, max_len: int = 1000) -> str | None:
    if raw is None:
        return None
    cleaned = raw
    for pat in _SECRET_PATTERNS:
        cleaned = pat.sub("[REDACTED]", cleaned)
    return cleaned[:max_len]


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return round(ordered[idx], 2)


class ObservabilityService:
    def __init__(self, *, session: AsyncSession, probe_client: ServiceProbeClient) -> None:
        self._session = session
        self._probe_client = probe_client

    async def get_platform_services_status(
        self,
        *,
        request_id: str | None = None,
    ) -> PlatformServicesStatusResponse:
        peer_snapshots = await self._probe_client.probe_all_peers(request_id=request_id)

        # Evaluate observability-service self-health directly to avoid recursive HTTP self-deadlocks
        db_ready = False
        try:
            db_ready = await check_database_connectivity(self._session)
        except Exception:
            db_ready = False

        self_snapshot = ServiceHealthSnapshot(
            service_name="observability-service",
            port=8012,
            health_status="healthy",
            ready_status="ready" if db_ready else "not_ready",
            health_http_status=200,
            ready_http_status=200 if db_ready else 503,
            latency_ms=0.1,
            detail=None if db_ready else "Database readiness check failed.",
        )

        snapshots = peer_snapshots + [self_snapshot]
        healthy_count = sum(1 for s in snapshots if s.health_status == "healthy")
        ready_count = sum(1 for s in snapshots if s.ready_status in {"ready", "unsupported"})
        unreachable_count = sum(1 for s in snapshots if s.health_status == "unreachable")
        degraded_count = len(snapshots) - healthy_count

        if healthy_count == len(snapshots) and ready_count == len(snapshots):
            overall = "healthy"
        elif healthy_count == 0:
            overall = "unhealthy"
        else:
            overall = "degraded"

        return PlatformServicesStatusResponse(
            overall_status=overall,
            checked_at=datetime.now(UTC),
            total_services=len(snapshots),
            healthy_count=healthy_count,
            ready_count=ready_count,
            degraded_count=degraded_count,
            unreachable_count=unreachable_count,
            services=snapshots,
        )

    async def record_telemetry(self, *, payload: TelemetryCreateRequest) -> TelemetryRecordResponse:
        is_error = payload.status_code >= 400 or bool(payload.error_code)
        rec = TelemetryRecord(
            service_name=payload.service_name.strip().lower(),
            route_path=scrub_text(payload.route_path.strip(), max_len=255) or "/",
            method=payload.method.strip().upper(),
            status_code=payload.status_code,
            duration_ms=round(payload.duration_ms, 2),
            is_error=is_error,
            error_code=payload.error_code.strip() if payload.error_code else None,
            error_summary=scrub_text(payload.error_summary),
            request_id=payload.request_id,
        )
        self._session.add(rec)
        await self._session.commit()
        await self._session.refresh(rec)
        return TelemetryRecordResponse.model_validate(rec)

    async def get_metrics_summary(self, *, service_name: str | None = None) -> PlatformMetricsResponse:
        stmt = select(TelemetryRecord)
        if service_name:
            stmt = stmt.where(TelemetryRecord.service_name == service_name.strip().lower())
        rows = list((await self._session.execute(stmt)).scalars().all())

        grouped: dict[str, list[TelemetryRecord]] = defaultdict(list)
        for row in rows:
            grouped[row.service_name].append(row)

        summaries: list[ServiceMetricSummary] = []
        for svc_name in sorted(grouped.keys()):
            items = grouped[svc_name]
            req_count = len(items)
            err_count = sum(1 for item in items if item.is_error)
            durations = [item.duration_ms for item in items]
            summaries.append(
                ServiceMetricSummary(
                    service_name=svc_name,
                    request_count=req_count,
                    error_count=err_count,
                    error_rate_percent=round((err_count / req_count) * 100.0, 2) if req_count else 0.0,
                    avg_latency_ms=round(sum(durations) / req_count, 2) if req_count else 0.0,
                    p95_latency_ms=_percentile_95(durations),
                    max_latency_ms=round(max(durations), 2) if durations else 0.0,
                )
            )

        return PlatformMetricsResponse(
            total_requests=len(rows),
            total_errors=sum(1 for row in rows if row.is_error),
            services=summaries,
        )

    async def list_recent_errors(
        self,
        *,
        service_name: str | None = None,
        limit: int = 50,
    ) -> ErrorLogListResponse:
        filters: list[Any] = [TelemetryRecord.is_error.is_(True)]
        if service_name:
            filters.append(TelemetryRecord.service_name == service_name.strip().lower())

        total = int(
            (await self._session.execute(select(func.count(TelemetryRecord.id)).where(*filters))).scalar_one()
        )
        rows = list(
            (
                await self._session.execute(
                    select(TelemetryRecord)
                    .where(*filters)
                    .order_by(TelemetryRecord.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return ErrorLogListResponse(
            items=[TelemetryRecordResponse.model_validate(row) for row in rows],
            total=total,
        )

