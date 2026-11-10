"""Tests for POST /api/v1/projects (creation, 1-to-1 requirements, validation, RBAC)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.project import Project, ProjectStatus
from app.models.project_requirement import ProjectRequirement
from tests.conftest import (
    ADMIN_TOKEN,
    FREELANCER_TOKEN,
    OWNER_ID,
    OWNER_TOKEN,
    auth_headers,
    build_project_payload,
)


@pytest.mark.asyncio
async def test_create_project_success_owner_creates_project_and_requirement(
    client: httpx.AsyncClient,
    project_app: FastAPI,
) -> None:
    payload = build_project_payload()
    response = await client.post("/api/v1/projects", headers=auth_headers(OWNER_TOKEN), json=payload)
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["owner_id"] == str(OWNER_ID)
    assert body["title"] == payload["title"]
    assert body["description"] == payload["description"]
    assert body["expected_result"] == payload["expected_result"]
    assert body["required_skills"] == ["Python", "FastAPI", "Webhooks"]
    assert body["budget"] == "5000.00"
    assert body["currency"] == "INR"
    assert body["status"] == ProjectStatus.OPEN.value
    assert body["published_at"] is None
    assert body["closed_at"] is None
    assert body["active_assignment"] is None

    async with project_app.state.session_factory() as session:
        stmt = select(Project).options(selectinload(Project.requirement))
        projects = list((await session.execute(stmt)).scalars().all())
        assert len(projects) == 1
        proj = projects[0]
        assert proj.owner_id == OWNER_ID
        assert proj.requirement is not None
        assert proj.requirement.project_id == proj.id
        assert proj.requirement.expected_result == payload["expected_result"]
        assert proj.requirement.required_skills == ["Python", "FastAPI", "Webhooks"]

        reqs = list((await session.execute(select(ProjectRequirement))).scalars().all())
        assert len(reqs) == 1


@pytest.mark.asyncio
async def test_create_project_normalizes_currency_budget_and_skills(
    client: httpx.AsyncClient,
) -> None:
    payload = build_project_payload(
        title="  Refactor   OAuth2   token rotation  ",
        budget=12500,
        currency="usd",
        required_skills=["  Python ", "python", "FastAPI", "   ", "Fast   API", "fast api"],
    )
    response = await client.post("/api/v1/projects", headers=auth_headers(OWNER_TOKEN), json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Refactor OAuth2 token rotation"
    assert body["budget"] == "12500.00"
    assert body["currency"] == "USD"
    assert body["required_skills"] == ["Python", "FastAPI", "Fast API"]


@pytest.mark.asyncio
async def test_create_project_rejects_freelancer_and_admin_with_403(
    client: httpx.AsyncClient,
    project_app: FastAPI,
) -> None:
    for token in (FREELANCER_TOKEN, ADMIN_TOKEN):
        response = await client.post(
            "/api/v1/projects",
            headers=auth_headers(token),
            json=build_project_payload(),
        )
        assert response.status_code == 403
        err = response.json()
        assert err["success"] is False
        assert err["error"]["code"] == "FORBIDDEN"

    async with project_app.state.session_factory() as session:
        projects = list((await session.execute(select(Project))).scalars().all())
        assert len(projects) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"title": "    "},
        {"title": "ab"},
        {"description": "   "},
        {"expected_result": "abc"},
        {"budget": "0"},
        {"budget": "-50.00"},
        {"budget": "5000.123"},
        {"budget": True},
        {"budget": "not-a-number"},
        {"currency": "IN"},
        {"currency": "123"},
        {"deadline": (date.today() - timedelta(days=1)).isoformat()},
        {"required_skills": []},
        {"required_skills": ["   ", ""]},
        {"required_skills": ["x" * 51]},
    ],
)
async def test_create_project_rejects_invalid_payload_fields(
    client: httpx.AsyncClient,
    overrides: dict[str, object],
) -> None:
    response = await client.post(
        "/api/v1/projects",
        headers=auth_headers(OWNER_TOKEN),
        json=build_project_payload(**overrides),
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_project_rejects_owner_id_or_status_spoofing(
    client: httpx.AsyncClient,
    project_app: FastAPI,
) -> None:
    for extra_payload in (
        {"owner_id": str(uuid.uuid4())},
        {"status": "ASSIGNED"},
        {"id": str(uuid.uuid4())},
    ):
        response = await client.post(
            "/api/v1/projects",
            headers=auth_headers(OWNER_TOKEN),
            json=build_project_payload(**extra_payload),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async with project_app.state.session_factory() as session:
        assert len(list((await session.execute(select(Project))).scalars().all())) == 0

