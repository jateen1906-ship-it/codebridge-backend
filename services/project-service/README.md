# CodeBridge Project Service (`project-service`)

> **Status:** Phase 3 implemented — standalone asynchronous FastAPI project lifecycle, requirements, discovery, and verified freelancer assignment microservice.

- **Default Port:** `8002`
- **Database Ownership:** `codebridge_project` (PostgreSQL with async `asyncpg` & Alembic)
- **Public Entry Point (via API Gateway):** `/api/v1/projects`, `/api/v1/projects/...`

---

## Responsibilities

- Project creation (`OWNER`-only) paired atomically with its 1-to-1 `ProjectRequirement` record (`expected_result`, normalized `required_skills`)
- Role-scoped project discovery and filtering (`OWNER`: own projects; `FREELANCER`: `OPEN` projects + projects actively assigned to them; `ADMIN`: all projects)
- Restricted partial project updates (`PATCH`) while unassigned and `OPEN` (forbids direct `id`, `owner_id`, and `status` mutation)
- Controlled project lifecycle state transitions via centralized `ProjectStateService`:
  - `OPEN` -> `ASSIGNED` -> `IN_PROGRESS` <-> `CHANGES_REQUESTED` -> `COMPLETED`
  - `OPEN` -> `CANCELLED` (unassigned projects only in Phase 3)
  - Future integration hooks for `workspace-service` and `review-service` (requiring `approval_verified=True` before marking `COMPLETED`)
- Owner-initiated freelancer selection (`POST /api/v1/projects/{project_id}/select-freelancer`) with synchronous Identity Service verification and database-level single-active-assignment concurrency protection

---

## Service Architecture & Identity Service Communication

```text
Caller ──▶ API Gateway (:8000) ──▶ Project Service (:8002)
                                       │
                                       ├── 1. AuthN: GET /api/v1/auth/me (forwards Bearer JWT + X-Request-ID)
                                       │      ──▶ Identity Service (:8001)
                                       │
                                       ├── 2. Freelancer Check: GET /api/v1/freelancers/{freelancer_id}
                                       │      (forwards X-Request-ID ONLY; never caller Authorization header)
                                       │      ──▶ Identity Service (:8001)
                                       │
                                       └── 3. Persistence: codebridge_project (PostgreSQL via asyncpg)
```

### Isolation & Zero-Trust Boundaries
- **Independent Databases:** Project Service never queries Identity Service's database. External Identity `User.id` UUIDs are stored in `projects.owner_id`, `assignments.freelancer_id`, and `assignments.assigned_by`.
- **Token Verification:** Project Service never parses or signs JWT secrets locally. Every protected route delegates Bearer token verification synchronously via pooled `IdentityClient` (`HTTPX`) to `GET {IDENTITY_SERVICE_URL}/api/v1/auth/me`, independently enforcing `is_active is True`.
- **Fail-Closed Availability:** Identity Service timeouts, connection errors, unexpected `5xx` responses, or malformed payloads fail closed with `503 IDENTITY_SERVICE_UNAVAILABLE` and never expose internal URLs or bearer tokens.
- **No Un-Released DB Locks During I/O:** `AssignmentService.select_freelancer` validates project eligibility, closes its read-only transaction before calling Identity Service, and opens its write lock (`SELECT ... FOR UPDATE` on PostgreSQL + partial unique index guard) only for the local atomic insert + status transition.

---

## Database Models & Relationships (`codebridge_project`)

| Table | Entity | Key Constraints & Indexes |
| :--- | :--- | :--- |
| `projects` | `Project` | PK `id` (`UUID`), indexed `owner_id`, `status` (`OPEN`, `ASSIGNED`, `IN_PROGRESS`, `CHANGES_REQUESTED`, `COMPLETED`, `CANCELLED`), `budget` (`NUMERIC(12,2)`, `CHECK (budget > 0)`), `currency`, indexed `deadline`, `created_at`, `updated_at`, `published_at`, `closed_at` |
| `project_requirements` | `ProjectRequirement` | PK `id` (`UUID`), unique FK `project_id` -> `projects.id` (`ON DELETE CASCADE`, 1-to-1 MVP), `expected_result` (`TEXT`), `required_skills` (`JSONB` on PostgreSQL / `JSON` variant) |
| `assignments` | `Assignment` | PK `id` (`UUID`), indexed FK `project_id` -> `projects.id` (`ON DELETE CASCADE`), indexed `freelancer_id`, `assigned_by`, indexed `status` (`ACTIVE`, `CANCELLED`, `COMPLETED`), **partial unique index** `uq_assignments_one_active_per_project` on `(project_id)` `WHERE status = 'ACTIVE'` |

---

## Folder Structure

```text
services/project-service/
├── app/
│   ├── __init__.py
│   ├── main.py                              # FastAPI factory, lifespan, middleware, /health & /api/v1 routers
│   ├── api/routes/
│   │   ├── projects.py                      # /api/v1/projects CRUD, publish, cancel routes
│   │   └── assignments.py                   # /api/v1/projects/{project_id}/select-freelancer
│   ├── clients/
│   │   └── identity_client.py               # Lifespan-managed HTTPX client for Identity Service
│   ├── core/
│   │   ├── config.py                        # Pydantic Settings with prod/staging DB & DEBUG guards
│   │   ├── database.py                      # Async SQLAlchemy engine & session factory
│   │   ├── dependencies.py                  # get_current_user, require_roles, require_owner/admin/any
│   │   ├── exceptions.py                    # Gateway-compatible standardized JSON error handlers
│   │   ├── logging.py                       # Structured JSON logger redacting tokens/secrets
│   │   └── security.py                      # AuthenticatedUser, UserRole, CorrelationIDMiddleware
│   ├── models/
│   │   ├── base.py
│   │   ├── project.py                       # Project & ProjectStatus
│   │   ├── project_requirement.py           # ProjectRequirement (1-to-1 with Project)
│   │   └── assignment.py                    # Assignment, AssignmentStatus & partial unique index
│   ├── repositories/
│   │   ├── project_repository.py            # Role-scoped SQL queries, search/skill/deadline filters
│   │   └── assignment_repository.py         # Active assignment lookup & creation
│   ├── schemas/
│   │   ├── assignment.py
│   │   ├── common.py                        # ErrorResponse & HealthResponse
│   │   ├── project.py                       # Strict Create/Update/List/Response schemas (MoneyDecimal)
│   │   └── requirements.py                  # Skill whitespace/case-insensitive deduplication
│   └── services/
│       ├── project_service.py               # Project CRUD, discovery, publish, cancel orchestration
│       ├── project_state_service.py         # Central state machine & future integration transition hooks
│       └── assignment_service.py            # Transactional assignment + race conflict handling
├── migrations/                              # Alembic async migration environment & initial schema revision
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── b9cee0566959_create_project_tables.py
├── tests/
│   ├── conftest.py
│   ├── test_assignments.py
│   ├── test_authorization.py
│   ├── test_identity_client.py
│   ├── test_project_creation.py
│   ├── test_project_details.py
│   ├── test_project_lifecycle.py
│   ├── test_project_listing.py
│   └── test_project_updates.py
├── .env.example
├── alembic.ini
├── pytest.ini
├── README.md
└── requirements.txt
```

> **Note on Alembic Path Layout:** Alembic files are organized in `migrations/` alongside `alembic.ini` (`script_location = migrations`), matching `services/identity-service/` in this monorepo.

---

## Authentication & Authorization Matrix

| Action / Endpoint | Unauthenticated | `OWNER` (Project Owner) | `OWNER` (Other Owner) | `FREELANCER` | `ADMIN` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `GET /health` | `200` | `200` | `200` | `200` | `200` |
| `POST /api/v1/projects` | `401` | `201` (Creates) | `201` (Own project) | `403` | `403` |
| `GET /api/v1/projects` | `401` | `200` (Own only) | `200` (Own only) | `200` (`OPEN` + own assigned) | `200` (All) |
| `GET /api/v1/projects/{id}` | `401` | `200` | `403` | `200` if `OPEN` or assigned else `403` | `200` |
| `PATCH /api/v1/projects/{id}` | `401` | `200` (when `OPEN`) / `409` | `403` | `403` | `403` |
| `POST /api/v1/projects/{id}/publish` | `401` | `200` (`OPEN` unpublished) / `409` | `403` | `403` | `403` |
| `POST /api/v1/projects/{id}/cancel` | `401` | `200` (`OPEN` unassigned) / `409` | `403` | `403` | `403` |
| `POST /api/v1/projects/{id}/select-freelancer` | `401` | `201` (`OPEN` valid freelancer) | `403` | `403` | `403` |

---

## API Endpoints & Examples

### 1. `POST /api/v1/projects` — Create Project (`OWNER`)
**Request:**
```json
{
  "title": "Fix Stripe payment webhook callback",
  "description": "Payment gateway returns 200 but final order status stays PENDING.",
  "expected_result": "Webhook verifies signature and marks order PAID idempotently.",
  "budget": "5000.00",
  "currency": "INR",
  "deadline": "2026-12-20",
  "required_skills": ["Python", "FastAPI", "Webhooks"]
}
```
**Response (`201 Created`):**
```json
{
  "id": "b62f8c7a-6334-4a36-9f88-112233445566",
  "owner_id": "11111111-1111-4111-8111-111111111111",
  "title": "Fix Stripe payment webhook callback",
  "description": "Payment gateway returns 200 but final order status stays PENDING.",
  "expected_result": "Webhook verifies signature and marks order PAID idempotently.",
  "required_skills": ["Python", "FastAPI", "Webhooks"],
  "budget": "5000.00",
  "currency": "INR",
  "deadline": "2026-12-20",
  "status": "OPEN",
  "created_at": "2026-11-10T12:00:00Z",
  "updated_at": "2026-11-10T12:00:00Z",
  "published_at": null,
  "closed_at": null,
  "active_assignment": null
}
```

### 2. `GET /api/v1/projects` — Role-Scoped Project Discovery
Query parameters: `page` (`>=1`, default `1`), `page_size` (`1..100`, default `20`), `search`, `status`, `skill` (exact case-insensitive tag match), `deadline_after`, `deadline_before`, `sort_by` (`created_at` | `deadline`), `sort_order` (`asc` | `desc`).

### 3. `GET /api/v1/projects/{project_id}` — Project Detail

### 4. `PATCH /api/v1/projects/{project_id}` — Update Permitted Fields (`OWNER`)
Updatable fields: `title`, `description`, `expected_result`, `budget`, `currency`, `deadline`, `required_skills`. Rejects `id`, `owner_id`, `status`, `published_at`, `closed_at`, `{}` empty patch, or `null` field values (`422 VALIDATION_ERROR`), and blocks updates once `ASSIGNED` or `CANCELLED` (`409 INVALID_PROJECT_STATUS`).

### 5. `POST /api/v1/projects/{project_id}/publish` — Publish Eligible Project (`OWNER`)
Validates completeness and sets `published_at` while remaining `OPEN`; rejects duplicate calls or non-`OPEN` projects with `409 INVALID_PROJECT_STATUS`.

### 6. `POST /api/v1/projects/{project_id}/cancel` — Cancel Unassigned Project (`OWNER`)
Transitions unassigned `OPEN` project to `CANCELLED` and sets `closed_at`; rejects `ASSIGNED` or already `CANCELLED` projects with `409 INVALID_PROJECT_STATUS`.

### 7. `POST /api/v1/projects/{project_id}/select-freelancer` — Assign Freelancer (`OWNER`)
**Request:**
```json
{
  "freelancer_id": "33333333-3333-4333-8333-333333333333"
}
```
> Note: `freelancer_id` must be the target freelancer's **Identity Service `User.id` UUID** (`user_id` from Identity's public freelancer response, not `FreelancerProfile.id`).

**Response (`201 Created`):**
```json
{
  "id": "a1b2c3d4-0000-4000-8000-000000000001",
  "project_id": "b62f8c7a-6334-4a36-9f88-112233445566",
  "freelancer_id": "33333333-3333-4333-8333-333333333333",
  "assigned_by": "11111111-1111-4111-8111-111111111111",
  "status": "ACTIVE",
  "project_status": "ASSIGNED",
  "assigned_at": "2026-11-10T12:05:00Z",
  "updated_at": "2026-11-10T12:05:00Z",
  "cancelled_at": null
}
```

### Standardized Error Codes
All errors conform to `{"success": false, "error": {"code": ..., "message": ...}, "request_id": "..."}`:
- `VALIDATION_ERROR` (`422`)
- `UNAUTHORIZED` (`401`)
- `FORBIDDEN` (`403`)
- `PROJECT_NOT_FOUND` (`404`)
- `PROJECT_ALREADY_ASSIGNED` (`409`)
- `INVALID_PROJECT_STATUS` (`409`)
- `FREELANCER_NOT_FOUND` (`404`)
- `INVALID_FREELANCER` (`400`)
- `IDENTITY_SERVICE_UNAVAILABLE` (`503`)
- `CONCURRENT_ASSIGNMENT_CONFLICT` (`409`)
- `INTERNAL_SERVER_ERROR` (`500`)

---

## Configuration

Copy `.env.example` to `.env` before running locally:

- `DATABASE_URL` — Async SQLAlchemy URL (`postgresql+asyncpg://...` required in `staging`/`production`)
- `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` — PostgreSQL pool settings (`10` / `20`)
- `IDENTITY_SERVICE_URL` — Base URL for Identity Service (`http://localhost:8001`)
- `IDENTITY_SERVICE_TIMEOUT_SECONDS` — Synchronous HTTPX timeout (`5.0`)
- `DEFAULT_CURRENCY` — Default 3-letter ISO currency (`INR`)
- `APP_ENV` / `DEBUG` — Production/staging block default DB URLs, SQLite URLs, and `DEBUG=true` (`DEBUG=release` is coerced safely to `false`)

---

## Installation, Migrations, Running Locally & Testing

```bash
# 1. Create and activate an isolated virtual environment inside services/project-service/
python -m venv .venv
# Windows: .venv\Scripts\activate  |  Linux/macOS: source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Apply database migrations (PostgreSQL in deployed environments)
alembic upgrade head

# 4. Run Project Service independently on port 8002
uvicorn app.main:app --host 0.0.0.0 --port 8002

# 5. Run the isolated async test suite
pytest -v
```

### PostgreSQL vs. Isolated SQLite Test Note
- Production migrations and PostgreSQL offline DDL (`alembic upgrade head --sql`) target PostgreSQL (`postgresql+asyncpg://`), using native `JSONB` for `project_requirements.required_skills`, `SELECT ... FOR UPDATE` row locking in `select_freelancer`, and the `postgresql_where=text("status = 'ACTIVE'")` partial unique index `uq_assignments_one_active_per_project`.
- The automated Pytest suite uses per-test isolated `sqlite+aiosqlite` temporary databases paired with `httpx.MockTransport` for deterministic unit and integration testing without requiring a live PostgreSQL instance (SQLite also supports the `sqlite_where=text("status = 'ACTIVE'")` partial unique index, allowing direct enforcement testing of `uq_assignments_one_active_per_project`). Live multi-service verification against PostgreSQL requires running PostgreSQL + Alembic `upgrade head` in each service environment.
