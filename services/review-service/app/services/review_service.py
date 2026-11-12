"""Domain workflow service enforcing submission, review, and Project Service synchronization."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.project_client import ProjectClient, ProjectSummary
from app.clients.workspace_client import TelemetryRelayClient, WorkspaceClient
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.security import AuthenticatedUser
from app.models import (
    Review,
    ReviewComment,
    ReviewDecision,
    ReviewEvent,
    Submission,
    SubmissionFile,
    SubmissionStatus,
)
from app.repositories.review_repository import ReviewRepository
from app.schemas.review import (
    CommentCreateRequest,
    ProjectReviewSummaryResponse,
    ReviewCommentResponse,
    ReviewDecisionRequest,
    ReviewResponse,
    SubmissionCreateRequest,
    SubmissionListResponse,
    SubmissionResponse,
)

ELIGIBLE_SUBMISSION_PROJECT_STATUSES = {"ASSIGNED", "IN_PROGRESS", "CHANGES_REQUESTED"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        project_client: ProjectClient,
        workspace_client: WorkspaceClient,
        telemetry_client: TelemetryRelayClient,
    ) -> None:
        self.session = session
        self.repo = ReviewRepository(session)
        self.project_client = project_client
        self.workspace_client = workspace_client
        self.telemetry_client = telemetry_client

    async def _require_project_member(
        self,
        *,
        project_id: uuid.UUID,
        user: AuthenticatedUser,
        request_id: str | None,
    ) -> ProjectSummary:
        project = await self.project_client.get_project(
            project_id=project_id,
            access_token=user.access_token,
            request_id=request_id,
        )
        if user.role == "ADMIN":
            return project
        if user.role == "OWNER" and project.owner_id == user.id:
            return project
        if (
            user.role == "FREELANCER"
            and project.assigned_freelancer_id == user.id
            and project.assignment_status in {"ACTIVE", "COMPLETED"}
        ):
            return project
        raise ForbiddenError("Only the project owner, assigned freelancer, or admin may access this project review.")

    async def create_submission(
        self,
        *,
        project_id: uuid.UUID,
        payload: SubmissionCreateRequest,
        freelancer: AuthenticatedUser,
        request_id: str | None,
    ) -> SubmissionResponse:
        if freelancer.role != "FREELANCER":
            raise ForbiddenError("Only an assigned freelancer may create a submission.")

        project = await self.project_client.get_project(
            project_id=project_id,
            access_token=freelancer.access_token,
            request_id=request_id,
        )
        if (
            project.assigned_freelancer_id != freelancer.id
            or project.assignment_status != "ACTIVE"
        ):
            raise ForbiddenError("Only the actively assigned freelancer may submit deliverables.")

        if project.status not in ELIGIBLE_SUBMISSION_PROJECT_STATUSES:
            raise ConflictError(
                f"Cannot submit deliverables while project is in {project.status} status.",
                error_code="PROJECT_NOT_ELIGIBLE_FOR_SUBMISSION",
            )

        existing_open = await self.repo.get_open_submission_for_project(project_id)
        if existing_open is not None:
            raise ConflictError(
                f"An open submission (v{existing_open.version_number}, status {existing_open.status}) already exists.",
                error_code="SUBMISSION_ALREADY_OPEN",
            )

        if payload.workspace_id is not None:
            await self.workspace_client.verify_workspace_belongs_to_project(
                workspace_id=payload.workspace_id,
                project_id=project_id,
                access_token=freelancer.access_token,
                request_id=request_id,
            )

        now = _utcnow()
        version_number = await self.repo.get_next_version_number(project_id)
        initial_status = (
            SubmissionStatus.SUBMITTED.value
            if payload.submit_immediately
            else SubmissionStatus.DRAFT.value
        )
        submission = Submission(
            project_id=project_id,
            freelancer_id=freelancer.id,
            owner_id=project.owner_id,
            workspace_id=payload.workspace_id,
            version_number=version_number,
            title=payload.title,
            summary=payload.summary,
            changes_summary=payload.changes_summary,
            test_results_summary=payload.test_results_summary,
            git_ref=payload.git_ref,
            commit_sha=payload.commit_sha,
            status=initial_status,
            submitted_at=now if payload.submit_immediately else None,
            created_at=now,
            updated_at=now,
        )
        for item in payload.files:
            submission.files.append(
                SubmissionFile(
                    file_path=item.file_path,
                    change_type=item.change_type,
                    summary=item.summary,
                )
            )
        self.repo.add_submission(submission)
        self.repo.add_event(
            ReviewEvent(
                submission=submission,
                project_id=project_id,
                actor_id=freelancer.id,
                event_type="SUBMISSION_SUBMITTED" if payload.submit_immediately else "SUBMISSION_DRAFT_CREATED",
                from_status=None,
                to_status=initial_status,
                detail=f"Submission v{version_number}: {payload.title}",
                request_id=request_id,
                created_at=now,
            )
        )

        try:
            await self.session.flush()
            if payload.submit_immediately and project.status in {"ASSIGNED", "CHANGES_REQUESTED"}:
                await self.project_client.transition_workflow(
                    project_id=project_id,
                    target_status="IN_PROGRESS",
                    approval_verified=False,
                    access_token=freelancer.access_token,
                    request_id=request_id,
                )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                "Concurrent submission conflict for project.",
                error_code="SUBMISSION_CONFLICT",
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

        created = await self.repo.get_submission_by_id(submission.id)
        assert created is not None

        if payload.submit_immediately:
            await self.telemetry_client.notify(
                user_id=project.owner_id,
                event_type="SUBMISSION_SUBMITTED",
                title="New Deliverable Submission Ready for Review",
                message=f"Submission v{version_number} ({created.title}) was submitted for project '{project.title}'.",
                project_id=project_id,
                idempotency_key=f"submission-submitted-{created.id}",
                request_id=request_id,
            )
        await self.telemetry_client.audit(
            actor_id=freelancer.id,
            actor_role=freelancer.role,
            action="SUBMISSION_CREATED",
            resource_type="submission",
            resource_id=str(created.id),
            project_id=project_id,
            metadata={"version_number": version_number, "status": created.status},
            request_id=request_id,
        )
        return SubmissionResponse.model_validate(created)

    async def list_submissions(
        self,
        *,
        project_id: uuid.UUID,
        user: AuthenticatedUser,
        request_id: str | None,
    ) -> SubmissionListResponse:
        await self._require_project_member(project_id=project_id, user=user, request_id=request_id)
        items = await self.repo.list_submissions_for_project(project_id)
        return SubmissionListResponse(
            project_id=project_id,
            total=len(items),
            items=[SubmissionResponse.model_validate(item) for item in items],
        )

    async def get_submission(
        self,
        *,
        submission_id: uuid.UUID,
        user: AuthenticatedUser,
        request_id: str | None,
    ) -> SubmissionResponse:
        submission = await self.repo.get_submission_by_id(submission_id)
        if submission is None:
            raise NotFoundError("Submission not found.", error_code="SUBMISSION_NOT_FOUND")
        await self._require_project_member(
            project_id=submission.project_id,
            user=user,
            request_id=request_id,
        )
        return SubmissionResponse.model_validate(submission)

    async def submit_draft(
        self,
        *,
        submission_id: uuid.UUID,
        freelancer: AuthenticatedUser,
        request_id: str | None,
    ) -> SubmissionResponse:
        submission = await self.repo.get_submission_by_id(submission_id)
        if submission is None:
            raise NotFoundError("Submission not found.", error_code="SUBMISSION_NOT_FOUND")
        if submission.freelancer_id != freelancer.id:
            raise ForbiddenError("Only the submission author may submit this draft.")
        if submission.status != SubmissionStatus.DRAFT.value:
            raise ConflictError(
                f"Cannot submit submission in {submission.status} status.",
                error_code="INVALID_SUBMISSION_STATUS",
            )

        project = await self.project_client.get_project(
            project_id=submission.project_id,
            access_token=freelancer.access_token,
            request_id=request_id,
        )
        now = _utcnow()
        submission.status = SubmissionStatus.SUBMITTED.value
        submission.submitted_at = now
        submission.updated_at = now
        self.repo.add_event(
            ReviewEvent(
                submission_id=submission.id,
                project_id=submission.project_id,
                actor_id=freelancer.id,
                event_type="SUBMISSION_SUBMITTED",
                from_status=SubmissionStatus.DRAFT.value,
                to_status=SubmissionStatus.SUBMITTED.value,
                detail="Draft submitted for owner review.",
                request_id=request_id,
                created_at=now,
            )
        )
        try:
            await self.session.flush()
            if project.status in {"ASSIGNED", "CHANGES_REQUESTED"}:
                await self.project_client.transition_workflow(
                    project_id=submission.project_id,
                    target_status="IN_PROGRESS",
                    approval_verified=False,
                    access_token=freelancer.access_token,
                    request_id=request_id,
                )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        updated = await self.repo.get_submission_by_id(submission_id)
        assert updated is not None
        await self.telemetry_client.notify(
            user_id=submission.owner_id,
            event_type="SUBMISSION_SUBMITTED",
            title="Deliverable Ready for Review",
            message=f"Submission v{updated.version_number} ({updated.title}) was submitted for project '{project.title}'.",
            project_id=submission.project_id,
            idempotency_key=f"submission-submitted-{updated.id}",
            request_id=request_id,
        )
        return SubmissionResponse.model_validate(updated)

    async def withdraw_submission(
        self,
        *,
        submission_id: uuid.UUID,
        freelancer: AuthenticatedUser,
        request_id: str | None,
    ) -> SubmissionResponse:
        submission = await self.repo.get_submission_by_id(submission_id)
        if submission is None:
            raise NotFoundError("Submission not found.", error_code="SUBMISSION_NOT_FOUND")
        if submission.freelancer_id != freelancer.id:
            raise ForbiddenError("Only the submission author may withdraw it.")
        if submission.status not in {SubmissionStatus.DRAFT.value, SubmissionStatus.SUBMITTED.value}:
            raise ConflictError(
                f"Cannot withdraw submission in {submission.status} status.",
                error_code="INVALID_SUBMISSION_STATUS",
            )

        previous_status = submission.status
        now = _utcnow()
        submission.status = SubmissionStatus.WITHDRAWN.value
        submission.updated_at = now
        self.repo.add_event(
            ReviewEvent(
                submission_id=submission.id,
                project_id=submission.project_id,
                actor_id=freelancer.id,
                event_type="SUBMISSION_WITHDRAWN",
                from_status=previous_status,
                to_status=SubmissionStatus.WITHDRAWN.value,
                detail="Submission withdrawn by freelancer.",
                request_id=request_id,
                created_at=now,
            )
        )
        await self.session.commit()
        updated = await self.repo.get_submission_by_id(submission_id)
        assert updated is not None
        return SubmissionResponse.model_validate(updated)

    async def add_comment(
        self,
        *,
        submission_id: uuid.UUID,
        payload: CommentCreateRequest,
        user: AuthenticatedUser,
        request_id: str | None,
    ) -> ReviewCommentResponse:
        submission = await self.repo.get_submission_by_id(submission_id)
        if submission is None:
            raise NotFoundError("Submission not found.", error_code="SUBMISSION_NOT_FOUND")
        await self._require_project_member(
            project_id=submission.project_id,
            user=user,
            request_id=request_id,
        )
        now = _utcnow()
        comment = ReviewComment(
            submission_id=submission.id,
            author_id=user.id,
            author_role=user.role,
            file_path=payload.file_path,
            line_number=payload.line_number,
            body=payload.body,
            created_at=now,
        )
        self.repo.add_comment(comment)
        self.repo.add_event(
            ReviewEvent(
                submission_id=submission.id,
                project_id=submission.project_id,
                actor_id=user.id,
                event_type="COMMENT_ADDED",
                from_status=submission.status,
                to_status=submission.status,
                detail=f"Comment posted by {user.role}.",
                request_id=request_id,
                created_at=now,
            )
        )
        await self.session.commit()
        await self.session.refresh(comment)
        return ReviewCommentResponse.model_validate(comment)

    async def start_review(
        self,
        *,
        submission_id: uuid.UUID,
        owner: AuthenticatedUser,
        request_id: str | None,
    ) -> SubmissionResponse:
        if owner.role != "OWNER":
            raise ForbiddenError("Only the project owner may start a review.")
        submission = await self.repo.get_submission_by_id(submission_id)
        if submission is None:
            raise NotFoundError("Submission not found.", error_code="SUBMISSION_NOT_FOUND")

        project = await self.project_client.get_project(
            project_id=submission.project_id,
            access_token=owner.access_token,
            request_id=request_id,
        )
        if project.owner_id != owner.id:
            raise ForbiddenError("Only the project owner may review submissions for this project.")

        if submission.status == SubmissionStatus.UNDER_REVIEW.value:
            return SubmissionResponse.model_validate(submission)
        if submission.status != SubmissionStatus.SUBMITTED.value:
            raise ConflictError(
                f"Cannot start review on submission in {submission.status} status.",
                error_code="INVALID_SUBMISSION_STATUS",
            )

        now = _utcnow()
        submission.status = SubmissionStatus.UNDER_REVIEW.value
        submission.updated_at = now
        self.repo.add_review(
            Review(
                submission_id=submission.id,
                project_id=submission.project_id,
                reviewer_id=owner.id,
                decision=ReviewDecision.PENDING.value,
                feedback="",
                checklist_passed=False,
                started_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        self.repo.add_event(
            ReviewEvent(
                submission_id=submission.id,
                project_id=submission.project_id,
                actor_id=owner.id,
                event_type="REVIEW_STARTED",
                from_status=SubmissionStatus.SUBMITTED.value,
                to_status=SubmissionStatus.UNDER_REVIEW.value,
                detail="Owner started reviewing submission.",
                request_id=request_id,
                created_at=now,
            )
        )
        await self.session.commit()
        updated = await self.repo.get_submission_by_id(submission_id)
        assert updated is not None
        return SubmissionResponse.model_validate(updated)

    async def decide_review(
        self,
        *,
        submission_id: uuid.UUID,
        payload: ReviewDecisionRequest,
        owner: AuthenticatedUser,
        request_id: str | None,
    ) -> SubmissionResponse:
        if owner.role != "OWNER":
            raise ForbiddenError("Only the project owner may record review decisions.")

        submission = await self.repo.get_submission_by_id(submission_id)
        if submission is None:
            raise NotFoundError("Submission not found.", error_code="SUBMISSION_NOT_FOUND")

        project = await self.project_client.get_project(
            project_id=submission.project_id,
            access_token=owner.access_token,
            request_id=request_id,
        )
        if project.owner_id != owner.id:
            raise ForbiddenError("Only the project owner may decide on this submission.")

        if submission.status not in {
            SubmissionStatus.SUBMITTED.value,
            SubmissionStatus.UNDER_REVIEW.value,
        }:
            raise ConflictError(
                f"Submission in {submission.status} status cannot be decided.",
                error_code="INVALID_SUBMISSION_STATUS",
            )

        if payload.decision == ReviewDecision.APPROVED.value and not payload.checklist_passed:
            raise ValidationError(
                "Approval requires checklist_passed=true.",
                error_code="APPROVAL_CHECKLIST_REQUIRED",
            )

        previous_status = submission.status
        now = _utcnow()
        review = await self.repo.get_latest_review_for_submission(submission.id)
        if review is None or review.decision != ReviewDecision.PENDING.value:
            review = Review(
                submission_id=submission.id,
                project_id=submission.project_id,
                reviewer_id=owner.id,
                decision=payload.decision,
                feedback=payload.feedback,
                checklist_passed=payload.checklist_passed,
                started_at=now,
                decided_at=now,
                created_at=now,
                updated_at=now,
            )
            self.repo.add_review(review)
        else:
            review.decision = payload.decision
            review.feedback = payload.feedback
            review.checklist_passed = payload.checklist_passed
            review.decided_at = now
            review.updated_at = now

        submission.status = payload.decision
        submission.reviewed_at = now
        submission.updated_at = now
        self.repo.add_event(
            ReviewEvent(
                submission_id=submission.id,
                project_id=submission.project_id,
                actor_id=owner.id,
                event_type=f"REVIEW_{payload.decision}",
                from_status=previous_status,
                to_status=payload.decision,
                detail=payload.feedback[:500],
                request_id=request_id,
                created_at=now,
            )
        )

        try:
            await self.session.flush()
            if payload.decision in {
                ReviewDecision.APPROVED.value,
                ReviewDecision.CHANGES_REQUESTED.value,
            }:
                if project.status in {"ASSIGNED", "CHANGES_REQUESTED"}:
                    await self.project_client.transition_workflow(
                        project_id=submission.project_id,
                        target_status="IN_PROGRESS",
                        approval_verified=False,
                        access_token=owner.access_token,
                        request_id=request_id,
                    )
                if payload.decision == ReviewDecision.APPROVED.value:
                    await self.project_client.transition_workflow(
                        project_id=submission.project_id,
                        target_status="COMPLETED",
                        approval_verified=True,
                        access_token=owner.access_token,
                        request_id=request_id,
                    )
                else:
                    await self.project_client.transition_workflow(
                        project_id=submission.project_id,
                        target_status="CHANGES_REQUESTED",
                        approval_verified=False,
                        access_token=owner.access_token,
                        request_id=request_id,
                    )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        updated = await self.repo.get_submission_by_id(submission_id)
        assert updated is not None

        await self.telemetry_client.notify(
            user_id=submission.freelancer_id,
            event_type=f"REVIEW_{payload.decision}",
            title=f"Review Decision: {payload.decision}",
            message=f"Owner recorded {payload.decision} on submission v{updated.version_number} for '{project.title}'.",
            project_id=submission.project_id,
            idempotency_key=f"review-decision-{updated.id}-{payload.decision}",
            request_id=request_id,
        )
        await self.telemetry_client.audit(
            actor_id=owner.id,
            actor_role=owner.role,
            action=f"REVIEW_{payload.decision}",
            resource_type="submission",
            resource_id=str(updated.id),
            project_id=submission.project_id,
            metadata={"decision": payload.decision, "version_number": updated.version_number},
            request_id=request_id,
        )
        return SubmissionResponse.model_validate(updated)

    async def get_project_review_summary(
        self,
        *,
        project_id: uuid.UUID,
        user: AuthenticatedUser,
        request_id: str | None,
    ) -> ProjectReviewSummaryResponse:
        project = await self._require_project_member(
            project_id=project_id,
            user=user,
            request_id=request_id,
        )
        submissions = await self.repo.list_submissions_for_project(project_id)
        approved_sub = next(
            (item for item in submissions if item.status == SubmissionStatus.APPROVED.value),
            None,
        )
        latest = submissions[0] if submissions else None
        return ProjectReviewSummaryResponse(
            project_id=project_id,
            owner_id=project.owner_id,
            approved=approved_sub is not None,
            approved_submission_id=approved_sub.id if approved_sub else None,
            approved_freelancer_id=approved_sub.freelancer_id if approved_sub else None,
            approved_at=approved_sub.reviewed_at if approved_sub else None,
            latest_submission_id=latest.id if latest else None,
            latest_status=latest.status if latest else None,
            submissions_count=len(submissions),
            items=[SubmissionResponse.model_validate(item) for item in submissions],
        )

