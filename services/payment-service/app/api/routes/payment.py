"""Payment & simulated escrow endpoints mounted under /api/v1/payment."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_authenticated, require_owner_or_admin
from app.core.security import AuthenticatedUser
from app.schemas.payment import (
    CreateEscrowRequest,
    EscrowActionRequest,
    EscrowListResponse,
    EscrowPaymentResponse,
    EscrowTransactionResponse,
    FundEscrowRequest,
    RefundEscrowRequest,
)
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payment", tags=["Payment & Escrow"])


def _service(request: Request, session: AsyncSession) -> PaymentService:
    return PaymentService(
        session=session,
        project_client=request.app.state.project_client,
        review_client=request.app.state.review_client,
        telemetry_client=request.app.state.telemetry_client,
    )


@router.post(
    "/projects/{project_id}/escrow",
    response_model=EscrowPaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_escrow(
    project_id: uuid.UUID,
    payload: CreateEscrowRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthenticatedUser = Depends(require_owner_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> EscrowPaymentResponse:
    return await _service(request, session).create_escrow(
        project_id=project_id,
        payload=payload,
        actor=actor,
        idempotency_key=idempotency_key,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/escrows/{escrow_id}/fund", response_model=EscrowPaymentResponse)
async def fund_escrow(
    escrow_id: uuid.UUID,
    payload: FundEscrowRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthenticatedUser = Depends(require_owner_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> EscrowPaymentResponse:
    return await _service(request, session).fund_escrow(
        escrow_id=escrow_id,
        payload=payload,
        actor=actor,
        idempotency_key=idempotency_key,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/escrows/{escrow_id}/hold", response_model=EscrowPaymentResponse)
async def hold_escrow(
    escrow_id: uuid.UUID,
    payload: EscrowActionRequest,
    request: Request,
    actor: AuthenticatedUser = Depends(require_owner_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> EscrowPaymentResponse:
    return await _service(request, session).hold_escrow(
        escrow_id=escrow_id,
        payload=payload,
        actor=actor,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/escrows/{escrow_id}/release", response_model=EscrowPaymentResponse)
async def release_escrow(
    escrow_id: uuid.UUID,
    payload: EscrowActionRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthenticatedUser = Depends(require_owner_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> EscrowPaymentResponse:
    return await _service(request, session).release_escrow(
        escrow_id=escrow_id,
        payload=payload,
        actor=actor,
        idempotency_key=idempotency_key,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/escrows/{escrow_id}/refund", response_model=EscrowPaymentResponse)
async def refund_escrow(
    escrow_id: uuid.UUID,
    payload: RefundEscrowRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthenticatedUser = Depends(require_owner_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> EscrowPaymentResponse:
    return await _service(request, session).refund_escrow(
        escrow_id=escrow_id,
        payload=payload,
        actor=actor,
        idempotency_key=idempotency_key,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/escrows/{escrow_id}/cancel", response_model=EscrowPaymentResponse)
async def cancel_escrow(
    escrow_id: uuid.UUID,
    payload: EscrowActionRequest,
    request: Request,
    actor: AuthenticatedUser = Depends(require_owner_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> EscrowPaymentResponse:
    return await _service(request, session).cancel_escrow(
        escrow_id=escrow_id,
        payload=payload,
        actor=actor,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/projects/{project_id}", response_model=EscrowListResponse)
async def list_project_escrows(
    project_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
) -> EscrowListResponse:
    return await _service(request, session).list_project_escrows(
        project_id=project_id,
        user=user,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/escrows/{escrow_id}", response_model=EscrowPaymentResponse)
async def get_escrow(
    escrow_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
) -> EscrowPaymentResponse:
    return await _service(request, session).get_escrow(
        escrow_id=escrow_id,
        user=user,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/escrows/{escrow_id}/transactions", response_model=list[EscrowTransactionResponse])
async def get_escrow_transactions(
    escrow_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
) -> list[EscrowTransactionResponse]:
    return await _service(request, session).get_escrow_transactions(
        escrow_id=escrow_id,
        user=user,
        request_id=getattr(request.state, "request_id", None),
    )
