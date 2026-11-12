"""Escrow financial workflow domain service enforcing server-side pricing, idempotency, and Review approval gates."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.service_clients import ProjectClient, ReviewClient, TelemetryRelayClient
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.security import AuthenticatedUser, hash_request_payload
from app.models import (
    EscrowPayment,
    EscrowStatus,
    EscrowTransaction,
    PaymentEvent,
    PaymentIdempotencyRecord,
    TransactionType,
)
from app.repositories.payment_repository import PaymentRepository
from app.schemas.payment import (
    CreateEscrowRequest,
    EscrowActionRequest,
    EscrowListResponse,
    EscrowPaymentResponse,
    EscrowTransactionResponse,
    FundEscrowRequest,
    RefundEscrowRequest,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        project_client: ProjectClient,
        review_client: ReviewClient,
        telemetry_client: TelemetryRelayClient,
    ) -> None:
        self.session = session
        self.repo = PaymentRepository(session)
        self.project_client = project_client
        self.review_client = review_client
        self.telemetry_client = telemetry_client

    async def _check_idempotency(
        self,
        *,
        actor_id: uuid.UUID,
        operation_key: str,
        idempotency_key: str | None,
        request_hash: str,
    ) -> EscrowPaymentResponse | None:
        if not idempotency_key:
            return None
        cleaned = idempotency_key.strip()
        if not cleaned:
            return None
        existing = await self.repo.get_idempotency_record(
            actor_id=actor_id,
            operation_key=operation_key,
            idempotency_key=cleaned,
        )
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise ConflictError(
                "Idempotency-Key was previously used with different request parameters.",
                error_code="IDEMPOTENCY_KEY_REUSE_CONFLICT",
            )
        return EscrowPaymentResponse.model_validate_json(existing.response_json)

    def _save_idempotency(
        self,
        *,
        actor_id: uuid.UUID,
        operation_key: str,
        idempotency_key: str | None,
        request_hash: str,
        status_code: int,
        response: EscrowPaymentResponse,
    ) -> None:
        if not idempotency_key or not idempotency_key.strip():
            return
        self.repo.add_idempotency_record(
            PaymentIdempotencyRecord(
                actor_id=actor_id,
                operation_key=operation_key,
                idempotency_key=idempotency_key.strip(),
                request_hash=request_hash,
                status_code=status_code,
                response_json=response.model_dump_json(),
            )
        )

    def _require_owner_or_admin_for_escrow(self, escrow: EscrowPayment, actor: AuthenticatedUser) -> None:
        if actor.role == "ADMIN":
            return
        if actor.role == "OWNER" and escrow.owner_id == actor.id:
            return
        raise ForbiddenError("Only the project owner or admin may manage this escrow.")

    async def create_escrow(
        self,
        *,
        project_id: uuid.UUID,
        payload: CreateEscrowRequest,
        actor: AuthenticatedUser,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> EscrowPaymentResponse:
        op_key = f"create:{project_id}"
        req_hash = hash_request_payload({"project_id": str(project_id), **payload.model_dump()})
        replay = await self._check_idempotency(
            actor_id=actor.id,
            operation_key=op_key,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
        if replay is not None:
            return replay

        project = await self.project_client.get_project(
            project_id=project_id,
            access_token=actor.access_token,
            request_id=request_id,
        )
        if actor.role != "ADMIN" and (actor.role != "OWNER" or project.owner_id != actor.id):
            raise ForbiddenError("Only the project owner or admin can create project escrow.")
        if project.status == "CANCELLED":
            raise ConflictError("Cannot create escrow for a cancelled project.", error_code="PROJECT_CANCELLED")

        if payload.amount is not None:
            client_amount = payload.amount.quantize(Decimal("0.01"))
            if client_amount != project.budget:
                raise ValidationError(
                    f"Escrow amount ({client_amount}) must match server project budget ({project.budget}).",
                    error_code="ESCROW_AMOUNT_MISMATCH",
                )
        if payload.currency is not None and payload.currency.upper() != project.currency:
            raise ValidationError(
                f"Escrow currency ({payload.currency.upper()}) must match project currency ({project.currency}).",
                error_code="ESCROW_CURRENCY_MISMATCH",
            )

        existing = await self.repo.get_active_escrow_for_project(project_id)
        if existing is not None:
            raise ConflictError(
                "An active escrow already exists for this project.",
                error_code="ESCROW_ALREADY_EXISTS",
            )

        now = _utcnow()
        status_val = EscrowStatus.CREATED.value
        funding_ref = None
        funded_at = None
        held_at = None
        if payload.fund_immediately:
            status_val = EscrowStatus.HELD.value if (payload.hold_immediately or project.assigned_freelancer_id) else EscrowStatus.FUNDED.value
            funding_ref = f"sim_fund_{uuid.uuid4().hex[:12]}"
            funded_at = now
            if status_val == EscrowStatus.HELD.value:
                held_at = now

        escrow = EscrowPayment(
            project_id=project_id,
            owner_id=project.owner_id,
            freelancer_id=project.assigned_freelancer_id,
            amount=project.budget,
            currency=project.currency,
            status=status_val,
            funding_reference=funding_ref,
            funded_at=funded_at,
            held_at=held_at,
            created_at=now,
            updated_at=now,
        )
        self.repo.add_escrow(escrow)
        self.repo.add_transaction(
            EscrowTransaction(
                escrow=escrow,
                project_id=project_id,
                actor_id=actor.id,
                transaction_type=TransactionType.CREATE.value,
                amount=project.budget,
                currency=project.currency,
                idempotency_key=idempotency_key,
                reference_id=f"esc_init_{uuid.uuid4().hex[:10]}",
                notes=payload.notes or "Simulated escrow created.",
                created_at=now,
            )
        )
        self.repo.add_event(
            PaymentEvent(
                escrow=escrow,
                actor_id=actor.id,
                from_status=None,
                to_status=EscrowStatus.CREATED.value,
                detail=f"Escrow initialized for {project.budget} {project.currency}.",
                request_id=request_id,
                created_at=now,
            )
        )
        if payload.fund_immediately:
            self.repo.add_transaction(
                EscrowTransaction(
                    escrow=escrow,
                    project_id=project_id,
                    actor_id=actor.id,
                    transaction_type=TransactionType.FUND.value,
                    amount=project.budget,
                    currency=project.currency,
                    idempotency_key=idempotency_key,
                    reference_id=funding_ref,
                    notes="Escrow funded on initialization.",
                    created_at=now,
                )
            )
            self.repo.add_event(
                PaymentEvent(
                    escrow=escrow,
                    actor_id=actor.id,
                    from_status=EscrowStatus.CREATED.value,
                    to_status=status_val,
                    detail=f"Escrow funded via simulated rail ({funding_ref}).",
                    request_id=request_id,
                    created_at=now,
                )
            )

        await self.session.flush()
        loaded = await self.repo.get_escrow_by_id(escrow.id)
        assert loaded is not None
        response = EscrowPaymentResponse.model_validate(loaded)
        self._save_idempotency(
            actor_id=actor.id,
            operation_key=op_key,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
            status_code=201,
            response=response,
        )
        await self.session.commit()

        await self.telemetry_client.audit(
            actor_id=actor.id,
            actor_role=actor.role,
            action="ESCROW_CREATED",
            resource_type="escrow_payment",
            resource_id=str(response.id),
            project_id=project_id,
            metadata={"amount": str(response.amount), "currency": response.currency, "status": response.status},
            request_id=request_id,
        )
        return response

    async def fund_escrow(
        self,
        *,
        escrow_id: uuid.UUID,
        payload: FundEscrowRequest,
        actor: AuthenticatedUser,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> EscrowPaymentResponse:
        op_key = f"fund:{escrow_id}"
        req_hash = hash_request_payload({"escrow_id": str(escrow_id), **payload.model_dump()})
        replay = await self._check_idempotency(
            actor_id=actor.id,
            operation_key=op_key,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
        if replay is not None:
            return replay

        escrow = await self.repo.get_escrow_by_id(escrow_id, for_update=True)
        if escrow is None:
            raise NotFoundError()
        self._require_owner_or_admin_for_escrow(escrow, actor)
        if escrow.status != EscrowStatus.CREATED.value:
            raise ConflictError(
                f"Cannot fund escrow in {escrow.status} status.",
                error_code="INVALID_ESCROW_STATE",
            )

        project = await self.project_client.get_project(
            project_id=escrow.project_id,
            access_token=actor.access_token,
            request_id=request_id,
        )
        if project.assigned_freelancer_id and not escrow.freelancer_id:
            escrow.freelancer_id = project.assigned_freelancer_id

        now = _utcnow()
        should_hold = payload.hold_if_assigned and escrow.freelancer_id is not None
        next_status = EscrowStatus.HELD.value if should_hold else EscrowStatus.FUNDED.value
        ref = f"sim_fund_{uuid.uuid4().hex[:12]}"

        escrow.status = next_status
        escrow.funding_reference = ref
        escrow.funded_at = now
        if should_hold:
            escrow.held_at = now
        escrow.updated_at = now

        self.repo.add_transaction(
            EscrowTransaction(
                escrow_id=escrow.id,
                project_id=escrow.project_id,
                actor_id=actor.id,
                transaction_type=TransactionType.FUND.value,
                amount=escrow.amount,
                currency=escrow.currency,
                idempotency_key=idempotency_key,
                reference_id=ref,
                notes=payload.notes or f"Funded via {payload.payment_method_reference}",
                created_at=now,
            )
        )
        self.repo.add_event(
            PaymentEvent(
                escrow_id=escrow.id,
                actor_id=actor.id,
                from_status=EscrowStatus.CREATED.value,
                to_status=next_status,
                detail=f"Escrow funded ({ref}).",
                request_id=request_id,
                created_at=now,
            )
        )
        await self.session.flush()
        loaded = await self.repo.get_escrow_by_id(escrow.id)
        assert loaded is not None
        response = EscrowPaymentResponse.model_validate(loaded)
        self._save_idempotency(
            actor_id=actor.id,
            operation_key=op_key,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
            status_code=200,
            response=response,
        )
        await self.session.commit()
        return response

    async def hold_escrow(
        self,
        *,
        escrow_id: uuid.UUID,
        payload: EscrowActionRequest,
        actor: AuthenticatedUser,
        request_id: str | None,
    ) -> EscrowPaymentResponse:
        escrow = await self.repo.get_escrow_by_id(escrow_id, for_update=True)
        if escrow is None:
            raise NotFoundError()
        self._require_owner_or_admin_for_escrow(escrow, actor)
        if escrow.status == EscrowStatus.HELD.value:
            return EscrowPaymentResponse.model_validate(escrow)
        if escrow.status != EscrowStatus.FUNDED.value:
            raise ConflictError(
                f"Only FUNDED escrow can be moved to HELD (current: {escrow.status}).",
                error_code="INVALID_ESCROW_STATE",
            )

        project = await self.project_client.get_project(
            project_id=escrow.project_id,
            access_token=actor.access_token,
            request_id=request_id,
        )
        if project.assigned_freelancer_id is None:
            raise ConflictError(
                "Cannot lock escrow into HELD before a freelancer is assigned.",
                error_code="FREELANCER_NOT_ASSIGNED",
            )

        now = _utcnow()
        escrow.freelancer_id = project.assigned_freelancer_id
        escrow.status = EscrowStatus.HELD.value
        escrow.held_at = now
        escrow.updated_at = now
        self.repo.add_transaction(
            EscrowTransaction(
                escrow_id=escrow.id,
                project_id=escrow.project_id,
                actor_id=actor.id,
                transaction_type=TransactionType.HOLD.value,
                amount=escrow.amount,
                currency=escrow.currency,
                reference_id=f"sim_hold_{uuid.uuid4().hex[:10]}",
                notes=payload.reason or "Escrow placed on hold for assigned freelancer.",
                created_at=now,
            )
        )
        self.repo.add_event(
            PaymentEvent(
                escrow_id=escrow.id,
                actor_id=actor.id,
                from_status=EscrowStatus.FUNDED.value,
                to_status=EscrowStatus.HELD.value,
                detail="Escrow funds locked in HELD state.",
                request_id=request_id,
                created_at=now,
            )
        )
        await self.session.commit()
        loaded = await self.repo.get_escrow_by_id(escrow.id)
        assert loaded is not None
        return EscrowPaymentResponse.model_validate(loaded)

    async def release_escrow(
        self,
        *,
        escrow_id: uuid.UUID,
        payload: EscrowActionRequest,
        actor: AuthenticatedUser,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> EscrowPaymentResponse:
        op_key = f"release:{escrow_id}"
        req_hash = hash_request_payload({"escrow_id": str(escrow_id), **payload.model_dump()})
        replay = await self._check_idempotency(
            actor_id=actor.id,
            operation_key=op_key,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
        if replay is not None:
            return replay

        escrow = await self.repo.get_escrow_by_id(escrow_id, for_update=True)
        if escrow is None:
            raise NotFoundError()
        self._require_owner_or_admin_for_escrow(escrow, actor)

        if escrow.status not in {
            EscrowStatus.FUNDED.value,
            EscrowStatus.HELD.value,
            EscrowStatus.RELEASE_PENDING.value,
        }:
            raise ConflictError(
                f"Cannot release escrow in {escrow.status} status.",
                error_code="INVALID_ESCROW_STATE",
            )

        approval = await self.review_client.require_project_approval(
            project_id=escrow.project_id,
            access_token=actor.access_token,
            request_id=request_id,
        )
        project = await self.project_client.get_project(
            project_id=escrow.project_id,
            access_token=actor.access_token,
            request_id=request_id,
        )
        payout_freelancer_id = approval.approved_freelancer_id or project.assigned_freelancer_id or escrow.freelancer_id
        if payout_freelancer_id is None:
            raise ConflictError(
                "No target freelancer could be resolved for escrow release.",
                error_code="FREELANCER_NOT_ASSIGNED",
            )

        previous_status = escrow.status
        now = _utcnow()
        release_ref = f"sim_rel_{uuid.uuid4().hex[:12]}"
        escrow.freelancer_id = payout_freelancer_id
        escrow.status = EscrowStatus.RELEASED.value
        escrow.release_reference = release_ref
        escrow.released_at = now
        escrow.updated_at = now

        self.repo.add_event(
            PaymentEvent(
                escrow_id=escrow.id,
                actor_id=actor.id,
                from_status=previous_status,
                to_status=EscrowStatus.RELEASE_PENDING.value,
                detail="Review approval confirmed; initiating simulated payout release.",
                request_id=request_id,
                created_at=now,
            )
        )
        self.repo.add_transaction(
            EscrowTransaction(
                escrow_id=escrow.id,
                project_id=escrow.project_id,
                actor_id=actor.id,
                transaction_type=TransactionType.RELEASE.value,
                amount=escrow.amount,
                currency=escrow.currency,
                idempotency_key=idempotency_key,
                reference_id=release_ref,
                notes=payload.reason or f"Escrow released for approved submission {approval.approved_submission_id}.",
                created_at=now,
            )
        )
        self.repo.add_event(
            PaymentEvent(
                escrow_id=escrow.id,
                actor_id=actor.id,
                from_status=EscrowStatus.RELEASE_PENDING.value,
                to_status=EscrowStatus.RELEASED.value,
                detail=f"Payout released ({release_ref}) to freelancer {payout_freelancer_id}.",
                request_id=request_id,
                created_at=now,
            )
        )

        await self.session.flush()
        loaded = await self.repo.get_escrow_by_id(escrow.id)
        assert loaded is not None
        response = EscrowPaymentResponse.model_validate(loaded)
        self._save_idempotency(
            actor_id=actor.id,
            operation_key=op_key,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
            status_code=200,
            response=response,
        )
        await self.session.commit()

        await self.telemetry_client.notify(
            user_id=payout_freelancer_id,
            event_type="ESCROW_RELEASED",
            title="Escrow Payment Released",
            message=f"Payment of {escrow.amount} {escrow.currency} was released for '{project.title}'.",
            project_id=escrow.project_id,
            idempotency_key=f"escrow-released-{escrow.id}",
            request_id=request_id,
        )
        await self.telemetry_client.audit(
            actor_id=actor.id,
            actor_role=actor.role,
            action="ESCROW_RELEASED",
            resource_type="escrow_payment",
            resource_id=str(escrow.id),
            project_id=escrow.project_id,
            metadata={"amount": str(escrow.amount), "currency": escrow.currency, "release_reference": release_ref},
            request_id=request_id,
        )
        return response

    async def refund_escrow(
        self,
        *,
        escrow_id: uuid.UUID,
        payload: RefundEscrowRequest,
        actor: AuthenticatedUser,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> EscrowPaymentResponse:
        op_key = f"refund:{escrow_id}"
        req_hash = hash_request_payload({"escrow_id": str(escrow_id), **payload.model_dump()})
        replay = await self._check_idempotency(
            actor_id=actor.id,
            operation_key=op_key,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
        if replay is not None:
            return replay

        escrow = await self.repo.get_escrow_by_id(escrow_id, for_update=True)
        if escrow is None:
            raise NotFoundError()
        self._require_owner_or_admin_for_escrow(escrow, actor)

        if escrow.status == EscrowStatus.RELEASED.value:
            raise ConflictError(
                "Cannot refund an escrow after funds have already been released.",
                error_code="ESCROW_ALREADY_RELEASED",
            )
        if escrow.status in {EscrowStatus.REFUNDED.value, EscrowStatus.CANCELLED.value}:
            raise ConflictError(
                f"Escrow is already in terminal {escrow.status} status.",
                error_code="INVALID_ESCROW_STATE",
            )
        if escrow.status == EscrowStatus.CREATED.value:
            raise ConflictError(
                "Unfunded CREATED escrow should be cancelled rather than refunded.",
                error_code="ESCROW_NOT_FUNDED",
            )

        previous_status = escrow.status
        now = _utcnow()
        refund_ref = f"sim_ref_{uuid.uuid4().hex[:12]}"
        next_status = EscrowStatus.REFUNDED.value if payload.process_immediately else EscrowStatus.REFUND_REQUESTED.value
        escrow.status = next_status
        escrow.refund_reference = refund_ref
        if payload.process_immediately:
            escrow.refunded_at = now
        escrow.updated_at = now

        self.repo.add_transaction(
            EscrowTransaction(
                escrow_id=escrow.id,
                project_id=escrow.project_id,
                actor_id=actor.id,
                transaction_type=(
                    TransactionType.REFUND.value
                    if payload.process_immediately
                    else TransactionType.REFUND_REQUEST.value
                ),
                amount=escrow.amount,
                currency=escrow.currency,
                idempotency_key=idempotency_key,
                reference_id=refund_ref,
                notes=payload.reason,
                created_at=now,
            )
        )
        self.repo.add_event(
            PaymentEvent(
                escrow_id=escrow.id,
                actor_id=actor.id,
                from_status=previous_status,
                to_status=next_status,
                detail=payload.reason,
                request_id=request_id,
                created_at=now,
            )
        )

        await self.session.flush()
        loaded = await self.repo.get_escrow_by_id(escrow.id)
        assert loaded is not None
        response = EscrowPaymentResponse.model_validate(loaded)
        self._save_idempotency(
            actor_id=actor.id,
            operation_key=op_key,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
            status_code=200,
            response=response,
        )
        await self.session.commit()
        return response

    async def cancel_escrow(
        self,
        *,
        escrow_id: uuid.UUID,
        payload: EscrowActionRequest,
        actor: AuthenticatedUser,
        request_id: str | None,
    ) -> EscrowPaymentResponse:
        escrow = await self.repo.get_escrow_by_id(escrow_id, for_update=True)
        if escrow is None:
            raise NotFoundError()
        self._require_owner_or_admin_for_escrow(escrow, actor)
        if escrow.status != EscrowStatus.CREATED.value:
            raise ConflictError(
                f"Only unfunded CREATED escrow can be cancelled (current: {escrow.status}).",
                error_code="INVALID_ESCROW_STATE",
            )
        now = _utcnow()
        escrow.status = EscrowStatus.CANCELLED.value
        escrow.cancelled_at = now
        escrow.updated_at = now
        self.repo.add_transaction(
            EscrowTransaction(
                escrow_id=escrow.id,
                project_id=escrow.project_id,
                actor_id=actor.id,
                transaction_type=TransactionType.CANCEL.value,
                amount=escrow.amount,
                currency=escrow.currency,
                notes=payload.reason or "Unfunded escrow cancelled.",
                created_at=now,
            )
        )
        self.repo.add_event(
            PaymentEvent(
                escrow_id=escrow.id,
                actor_id=actor.id,
                from_status=EscrowStatus.CREATED.value,
                to_status=EscrowStatus.CANCELLED.value,
                detail=payload.reason or "Escrow cancelled before funding.",
                request_id=request_id,
                created_at=now,
            )
        )
        await self.session.commit()
        loaded = await self.repo.get_escrow_by_id(escrow.id)
        assert loaded is not None
        return EscrowPaymentResponse.model_validate(loaded)

    async def _require_read_access(
        self,
        *,
        escrow: EscrowPayment,
        user: AuthenticatedUser,
        request_id: str | None,
    ) -> None:
        if user.role == "ADMIN" or escrow.owner_id == user.id or escrow.freelancer_id == user.id:
            return
        project = await self.project_client.get_project(
            project_id=escrow.project_id,
            access_token=user.access_token,
            request_id=request_id,
        )
        if project.owner_id == user.id or project.assigned_freelancer_id == user.id:
            return
        raise ForbiddenError("You do not have permission to view this escrow record.")

    async def get_escrow(
        self,
        *,
        escrow_id: uuid.UUID,
        user: AuthenticatedUser,
        request_id: str | None,
    ) -> EscrowPaymentResponse:
        escrow = await self.repo.get_escrow_by_id(escrow_id)
        if escrow is None:
            raise NotFoundError()
        await self._require_read_access(escrow=escrow, user=user, request_id=request_id)
        return EscrowPaymentResponse.model_validate(escrow)

    async def list_project_escrows(
        self,
        *,
        project_id: uuid.UUID,
        user: AuthenticatedUser,
        request_id: str | None,
    ) -> EscrowListResponse:
        project = await self.project_client.get_project(
            project_id=project_id,
            access_token=user.access_token,
            request_id=request_id,
        )
        if (
            user.role != "ADMIN"
            and project.owner_id != user.id
            and project.assigned_freelancer_id != user.id
        ):
            raise ForbiddenError("You do not have permission to inspect project escrows.")
        escrows = await self.repo.list_escrows_for_project(project_id)
        return EscrowListResponse(
            project_id=project_id,
            total=len(escrows),
            items=[EscrowPaymentResponse.model_validate(e) for e in escrows],
        )

    async def get_escrow_transactions(
        self,
        *,
        escrow_id: uuid.UUID,
        user: AuthenticatedUser,
        request_id: str | None,
    ) -> list[EscrowTransactionResponse]:
        escrow = await self.repo.get_escrow_by_id(escrow_id)
        if escrow is None:
            raise NotFoundError()
        await self._require_read_access(escrow=escrow, user=user, request_id=request_id)
        txns = await self.repo.list_transactions(escrow_id)
        return [EscrowTransactionResponse.model_validate(t) for t in txns]

