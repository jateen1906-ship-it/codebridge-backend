"""Tests for public freelancer discovery, filtering, pagination, and self-updates."""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from app.models.user import User
from tests.conftest import login_user, register_user


async def _register_and_update_freelancer(
    client: httpx.AsyncClient,
    *,
    full_name: str,
    email: str,
    password: str = "FreelancerPass123!",
    bio: str,
    skills: list[str],
    experience_years: int,
    availability: str,
) -> dict:
    reg = await register_user(
        client,
        full_name=full_name,
        email=email,
        password=password,
        role="FREELANCER",
    )
    assert reg.status_code == 201
    token = (await login_user(client, email=email, password=password)).json()["access_token"]
    upd = await client.patch(
        "/api/v1/freelancers/me",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "bio": bio,
            "skills": skills,
            "experience_years": experience_years,
            "availability": availability,
        },
    )
    assert upd.status_code == 200
    return {"user": reg.json(), "profile": upd.json()}


@pytest.mark.asyncio
async def test_freelancer_self_update_normalizes_skills(client: httpx.AsyncClient) -> None:
    created = await _register_and_update_freelancer(
        client,
        full_name="Maya Engineer",
        email="maya@example.com",
        bio="  Backend specialist focusing on resilient APIs.  ",
        skills=["  Python  ", "python", "FastAPI", "", "PostgreSQL"],
        experience_years=5,
        availability="BUSY",
    )
    profile = created["profile"]
    assert profile["bio"] == "Backend specialist focusing on resilient APIs."
    assert profile["skills"] == ["Python", "FastAPI", "PostgreSQL"]
    assert profile["experience_years"] == 5
    assert profile["availability"] == "BUSY"


@pytest.mark.asyncio
async def test_public_freelancers_filtering_pagination_and_privacy(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    # Seed non-freelancer owner to verify only freelancers appear publicly.
    await register_user(
        client,
        full_name="Hidden Owner",
        email="hidden.owner@example.com",
        password="OwnerPassword123!",
        role="OWNER",
    )

    f1 = await _register_and_update_freelancer(
        client,
        full_name="Alice Pythonista",
        email="alice.secret@example.com",
        bio="Python and FastAPI expert",
        skills=["Python", "FastAPI", "PostgreSQL"],
        experience_years=6,
        availability="AVAILABLE",
    )
    f2 = await _register_and_update_freelancer(
        client,
        full_name="Bob ReactDev",
        email="bob.secret@example.com",
        bio="Frontend specialist",
        skills=["React", "TypeScript", "Fast"],
        experience_years=4,
        availability="BUSY",
    )
    f3 = await _register_and_update_freelancer(
        client,
        full_name="Clara DataEng",
        email="clara.secret@example.com",
        bio="Async Python services",
        skills=["Python", "FastAPI"],
        experience_years=3,
        availability="UNAVAILABLE",
    )
    await _register_and_update_freelancer(
        client,
        full_name="Deactivated Freelancer",
        email="deactivated.free@example.com",
        bio="Should not be listed",
        skills=["Python"],
        experience_years=2,
        availability="AVAILABLE",
    )
    async with identity_app.state.session_factory() as session:
        await session.execute(
            update(User)
            .where(User.email == "deactivated.free@example.com")
            .values(is_active=False)
        )
        await session.commit()

    # List active freelancers with pagination
    page1 = await client.get("/api/v1/freelancers", params={"page": 1, "page_size": 2})
    assert page1.status_code == 200
    data1 = page1.json()
    assert data1["total"] == 3
    assert data1["page"] == 1
    assert data1["page_size"] == 2
    assert data1["total_pages"] == 2
    assert len(data1["items"]) == 2

    # Verify zero leakage of private email or auth fields
    for item in data1["items"]:
        assert "email" not in item
        assert "password_hash" not in item
        assert "refresh_tokens" not in item

    # Exact case-insensitive skill filter: 'fastapi' matches f1 and f3, NOT f2 ('Fast')
    skill_res = await client.get("/api/v1/freelancers", params={"skill": "fastapi"})
    assert skill_res.status_code == 200
    skill_data = skill_res.json()
    assert skill_data["total"] == 2
    returned_names = {item["full_name"] for item in skill_data["items"]}
    assert returned_names == {"Alice Pythonista", "Clara DataEng"}

    # Name search + availability filter
    filtered_res = await client.get(
        "/api/v1/freelancers",
        params={"search": "alice", "availability": "AVAILABLE"},
    )
    assert filtered_res.status_code == 200
    assert filtered_res.json()["total"] == 1
    assert filtered_res.json()["items"][0]["id"] == f1["profile"]["id"]

    # Availability only
    busy_res = await client.get("/api/v1/freelancers", params={"availability": "BUSY"})
    assert busy_res.status_code == 200
    assert busy_res.json()["total"] == 1
    assert busy_res.json()["items"][0]["id"] == f2["profile"]["id"]

    # Retrieve detail by profile ID and by user ID without auth and without exposing email
    detail_by_profile = await client.get(f"/api/v1/freelancers/{f3['profile']['id']}")
    assert detail_by_profile.status_code == 200
    assert detail_by_profile.json()["full_name"] == "Clara DataEng"
    assert "email" not in detail_by_profile.json()

    detail_by_user = await client.get(f"/api/v1/freelancers/{f3['user']['id']}")
    assert detail_by_user.status_code == 200
    assert detail_by_user.json()["id"] == f3["profile"]["id"]


@pytest.mark.asyncio
async def test_get_nonexistent_freelancer_returns_404(client: httpx.AsyncClient) -> None:
    res = await client.get(f"/api/v1/freelancers/{uuid.uuid4()}")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "USER_NOT_FOUND"
