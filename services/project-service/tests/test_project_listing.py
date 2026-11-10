"""Tests for GET /api/v1/projects role visibility scoping, pagination, filtering, and sorting."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from app.models.assignment import Assignment, AssignmentStatus
from app.models.project import Project, ProjectStatus
from tests.conftest import (
    ADMIN_TOKEN,
    FREELANCER_2_ID,
    FREELANCER_2_TOKEN,
    FREELANCER_ID,
    FREELANCER_TOKEN,
    OWNER_2_ID,
    OWNER_2_TOKEN,
    OWNER_ID,
    OWNER_TOKEN,
    auth_headers,
    create_sample_project,
    future_deadline,
)


@pytest.mark.asyncio
async def test_list_projects_enforces_strict_role_visibility(
    client: httpx.AsyncClient,
    project_app: FastAPI,
) -> None:
    o1_open = await create_sample_project(client, token=OWNER_TOKEN, title="Owner1 Open Project")
    o1_assigned_f1 = await create_sample_project(
        client, token=OWNER_TOKEN, title="Owner1 Assigned F1"
    )
    o1_assigned_f2 = await create_sample_project(
        client, token=OWNER_TOKEN, title="Owner1 Assigned F2"
    )
    o1_cancelled = await create_sample_project(
        client, token=OWNER_TOKEN, title="Owner1 Cancelled Project"
    )
    o2_open = await create_sample_project(client, token=OWNER_2_TOKEN, title="Owner2 Open Project")

    assign1 = await client.post(
        f"/api/v1/projects/{o1_assigned_f1['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    assert assign1.status_code == 201

    assign2 = await client.post(
        f"/api/v1/projects/{o1_assigned_f2['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_2_ID)},
    )
    assert assign2.status_code == 201

    cancel_res = await client.post(
        f"/api/v1/projects/{o1_cancelled['id']}/cancel",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert cancel_res.status_code == 200

    # Inject a historical CANCELLED assignment for FREELANCER_ID on o1_cancelled to ensure
    # cancelled assignment history NEVER exposes cancelled or re-assigned projects.
    async with project_app.state.session_factory() as session:
        session.add(
            Assignment(
                project_id=uuid.UUID(o1_cancelled["id"]),
                freelancer_id=FREELANCER_ID,
                assigned_by=OWNER_ID,
                status=AssignmentStatus.CANCELLED,
                cancelled_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    # OWNER 1 sees only Owner 1's 4 projects
    owner1_list = await client.get("/api/v1/projects", headers=auth_headers(OWNER_TOKEN))
    assert owner1_list.status_code == 200
    o1_ids = {item["id"] for item in owner1_list.json()["items"]}
    assert o1_ids == {o1_open["id"], o1_assigned_f1["id"], o1_assigned_f2["id"], o1_cancelled["id"]}

    # OWNER 2 sees only Owner 2's project
    owner2_list = await client.get("/api/v1/projects", headers=auth_headers(OWNER_2_TOKEN))
    assert owner2_list.status_code == 200
    assert {item["id"] for item in owner2_list.json()["items"]} == {o2_open["id"]}

    # FREELANCER 1 sees both OPEN projects + project assigned to F1 (never F2's, never CANCELLED)
    f1_list = await client.get("/api/v1/projects", headers=auth_headers(FREELANCER_TOKEN))
    assert f1_list.status_code == 200
    f1_ids = {item["id"] for item in f1_list.json()["items"]}
    assert f1_ids == {o1_open["id"], o2_open["id"], o1_assigned_f1["id"]}

    # FREELANCER 2 sees OPEN projects + F2's assigned project (never F1's)
    f2_list = await client.get("/api/v1/projects", headers=auth_headers(FREELANCER_2_TOKEN))
    assert f2_list.status_code == 200
    assert {item["id"] for item in f2_list.json()["items"]} == {
        o1_open["id"],
        o2_open["id"],
        o1_assigned_f2["id"],
    }

    # ADMIN sees all 5 projects across both owners
    admin_list = await client.get("/api/v1/projects", headers=auth_headers(ADMIN_TOKEN))
    assert admin_list.status_code == 200
    assert admin_list.json()["total"] == 5
    assert {item["owner_id"] for item in admin_list.json()["items"]} == {
        str(OWNER_ID),
        str(OWNER_2_ID),
    }


@pytest.mark.asyncio
async def test_list_projects_pagination_and_sorting(
    client: httpx.AsyncClient,
    project_app: FastAPI,
) -> None:
    created: list[dict] = []
    deadlines = [15, 5, 25, 10, 20]
    for idx, dl in enumerate(deadlines):
        proj = await create_sample_project(
            client,
            token=OWNER_TOKEN,
            title=f"Sorted Project {idx}",
            deadline=future_deadline(dl),
        )
        created.append(proj)

    # Stagger created_at timestamps deterministically
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with project_app.state.session_factory() as session:
        for idx, proj in enumerate(created):
            await session.execute(
                update(Project)
                .where(Project.id == uuid.UUID(proj["id"]))
                .values(created_at=base_ts + timedelta(hours=idx))
            )
        await session.commit()

    page1 = await client.get(
        "/api/v1/projects?page=1&page_size=2&sort_by=deadline&sort_order=asc",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert page1.status_code == 200
    p1 = page1.json()
    assert p1["total"] == 5
    assert p1["total_pages"] == 3
    assert [item["title"] for item in p1["items"]] == ["Sorted Project 1", "Sorted Project 3"]

    page3 = await client.get(
        "/api/v1/projects?page=3&page_size=2&sort_by=deadline&sort_order=asc",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert page3.status_code == 200
    assert [item["title"] for item in page3.json()["items"]] == ["Sorted Project 2"]

    beyond = await client.get(
        "/api/v1/projects?page=4&page_size=2",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert beyond.status_code == 200
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 5


@pytest.mark.asyncio
async def test_list_projects_search_exact_skill_status_and_deadline_filters(
    client: httpx.AsyncClient,
) -> None:
    p_fastapi = await create_sample_project(
        client,
        token=OWNER_TOKEN,
        title="FastAPI Stripe Webhook",
        description="Fix HMAC validation in backend service",
        required_skills=["Python", "FastAPI"],
        deadline=future_deadline(10),
    )
    p_fast_only = await create_sample_project(
        client,
        token=OWNER_TOKEN,
        title="Frontend Speed Optimization",
        description="Improve web vitals without FastAPI",
        required_skills=["Fast", "React"],
        deadline=future_deadline(30),
    )
    p_django = await create_sample_project(
        client,
        token=OWNER_2_TOKEN,
        title="Django Admin Dashboard",
        description="Repair export webhook jobs",
        required_skills=["Python", "Django"],
        deadline=future_deadline(20),
    )

    await client.post(
        f"/api/v1/projects/{p_fastapi['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )

    # Search matches title OR description across visible projects
    search_res = await client.get(
        "/api/v1/projects?search=webhook",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert search_res.status_code == 200
    assert {item["id"] for item in search_res.json()["items"]} == {p_fastapi["id"], p_django["id"]}

    # Exact case-insensitive skill filter: 'fastapi' matches FastAPI, NOT 'Fast'
    skill_res = await client.get(
        "/api/v1/projects?skill=fastapi",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert skill_res.status_code == 200
    assert [item["id"] for item in skill_res.json()["items"]] == [p_fastapi["id"]]

    # 'fast' matches ONLY 'Fast' tag, NOT 'FastAPI'
    fast_skill_res = await client.get(
        "/api/v1/projects?skill=FAST",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert fast_skill_res.status_code == 200
    assert [item["id"] for item in fast_skill_res.json()["items"]] == [p_fast_only["id"]]

    # Status filter respects Freelancer assignment visibility (F2 cannot see F1's ASSIGNED project)
    f2_assigned_filter = await client.get(
        f"/api/v1/projects?status={ProjectStatus.ASSIGNED.value}",
        headers=auth_headers(FREELANCER_2_TOKEN),
    )
    assert f2_assigned_filter.status_code == 200
    assert f2_assigned_filter.json()["items"] == []

    # Deadline range filtering
    range_res = await client.get(
        f"/api/v1/projects?deadline_after={future_deadline(15)}&deadline_before={future_deadline(25)}",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert range_res.status_code == 200
    assert [item["id"] for item in range_res.json()["items"]] == [p_django["id"]]

    # Invalid date range returns 422 VALIDATION_ERROR
    bad_range = await client.get(
        f"/api/v1/projects?deadline_after={future_deadline(30)}&deadline_before={future_deadline(10)}",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert bad_range.status_code == 422
    assert bad_range.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_list_projects_empty_result_returns_zero_totals(
    client: httpx.AsyncClient,
) -> None:
    empty_res = await client.get("/api/v1/projects", headers=auth_headers(OWNER_TOKEN))
    assert empty_res.status_code == 200
    assert empty_res.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "total_pages": 0,
    }

