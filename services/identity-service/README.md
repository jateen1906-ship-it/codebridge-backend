# CodeBridge Identity Service (`identity-service`)

> **Status:** Phase 2 implemented — standalone asynchronous FastAPI authentication, RBAC, profile, and freelancer discovery microservice.

- **Default Port:** `8001`
- **Database Ownership:** `codebridge_identity` (PostgreSQL with async `asyncpg` & Alembic)
- **Public Entry Point (via API Gateway):** `/api/v1/auth/...`, `/api/v1/profile/...`, `/api/v1/freelancers/...`

## Responsibilities

- User registration (`OWNER` and `FREELANCER` public registration; `ADMIN` strictly blocked publicly)
- Credential authentication with Argon2 password hashing (`pwdlib[argon2]`)
- Signed JWT access tokens (short-lived) and refresh tokens (PyJWT)
- Refresh token rotation, SHA-256 `jti_hash` persistence, family-wide reuse detection, and logout revocation
- Role-based access control (`OWNER`, `FREELANCER`, `ADMIN`) with reusable FastAPI dependencies
- Authenticated user profile management (`GET /api/v1/profile`, `PATCH /api/v1/profile`)
- Public freelancer discovery (`GET /api/v1/freelancers`, `GET /api/v1/freelancers/{freelancer_id}`) and `FREELANCER` self-management (`PATCH /api/v1/freelancers/me`)
- Out-of-band CLI admin provisioning (`app.core.bootstrap_admin`) and `ADMIN`-only user management routes

## Layered Architecture

```text
HTTP Request (Direct or via API Gateway :8000)
       │  CorrelationIDMiddleware (X-Request-ID) + Standardized Error Handlers
       ▼
  app/api/routes/          # HTTP routing, OpenAPI contracts, RBAC dependencies
       │
       ▼
  app/services/            # Business logic (AuthService, TokenService, UserService, ProfileService)
       │
       ▼
  app/repositories/        # Async SQLAlchemy 2.x data access (User, Profile, Token repositories)
       │
       ▼
  app/models/              # ORM entities (users, freelancer_profiles, refresh_tokens)
```

## Folder Structure

```text
services/identity-service/
├── app/
│   ├── __init__.py
│   ├── main.py                        # FastAPI app factory, lifespan, middleware, /health & /api/v1 routers
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── auth.py                # /api/v1/auth/* and /api/v1/auth/admin/* routes
│   │       ├── profile.py             # /api/v1/profile GET & PATCH routes
│   │       └── freelancers.py         # /api/v1/freelancers public discovery & /me update
│   ├── core/
│   │   ├── __init__.py
│   │   ├── bootstrap_admin.py         # Out-of-band CLI script to create initial ADMIN account
│   │   ├── config.py                  # Pydantic Settings with prod/staging secret guards
│   │   ├── correlation.py             # ASGI X-Request-ID validation & propagation middleware
│   │   ├── database.py                # Async SQLAlchemy engine & session factory
│   │   ├── dependencies.py            # get_current_user, require_roles, require_owner/freelancer/admin
│   │   ├── exceptions.py              # Gateway-compatible JSON error handlers
│   │   ├── logging.py                 # Structured JSON logger with secret key redaction
│   │   └── security.py                # Argon2 hashing, SHA-256 JTI hashing, PyJWT helpers
│   ├── models/
│   │   ├── base.py
│   │   ├── freelancer_profile.py      # FreelancerProfile + AvailabilityStatus enum
│   │   ├── refresh_token.py           # RefreshToken (hashed JTI + family_id + replacement link)
│   │   └── user.py                    # User + UserRole enum (OWNER, FREELANCER, ADMIN)
│   ├── repositories/
│   │   ├── profile_repository.py
│   │   ├── token_repository.py
│   │   └── user_repository.py
│   ├── schemas/
│   │   ├── auth.py
│   │   ├── common.py
│   │   ├── freelancer.py
│   │   ├── profile.py
│   │   └── user.py
│   └── services/
│       ├── auth_service.py
│       ├── profile_service.py
│       ├── token_service.py
│       └── user_service.py
├── migrations/
│   ├── env.py                         # Async Alembic environment
│   ├── script.py.mako
│   └── versions/
│       └── 66dd534d7261_create_identity_tables.py
├── tests/
│   ├── conftest.py
│   ├── test_freelancers.py
│   ├── test_login.py
│   ├── test_profiles.py
│   ├── test_rbac.py
│   ├── test_registration.py
│   ├── test_security.py
│   └── test_tokens.py
├── .env.example
├── alembic.ini
├── Dockerfile
├── README.md
└── requirements.txt
```

## API Endpoints

| Method | Path | Auth & Role | Summary |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Public | Service liveness health check |
| `POST` | `/api/v1/auth/register` | Public | Register `OWNER` or `FREELANCER` (`ADMIN` rejected with `403`) |
| `POST` | `/api/v1/auth/login` | Public | Authenticate credentials; returns access + refresh JWTs |
| `POST` | `/api/v1/auth/refresh` | Refresh JWT | Rotate refresh token; detects reuse and revokes token family |
| `POST` | `/api/v1/auth/logout` | Access JWT | Revoke caller's refresh token session family |
| `GET` | `/api/v1/auth/me` | Access JWT | Current authenticated user identity |
| `GET` | `/api/v1/profile` | Access JWT | Current user profile (includes `freelancer_profile` for freelancers) |
| `PATCH` | `/api/v1/profile` | Access JWT | Update permitted profile fields (`full_name` only; forbids `role`/flags) |
| `GET` | `/api/v1/freelancers` | Public | Paginated freelancer discovery (`search`, `skill`, `availability`) |
| `GET` | `/api/v1/freelancers/{freelancer_id}` | Public | Single active freelancer public profile (never exposes email) |
| `PATCH` | `/api/v1/freelancers/me` | Access JWT (`FREELANCER`) | Update `bio`, `skills`, `experience_years`, `availability` |
| `POST` | `/api/v1/auth/admin/users` | Access JWT (`ADMIN`) | Restricted administrative account provisioning |
| `GET` | `/api/v1/auth/admin/users` | Access JWT (`ADMIN`) | List accounts with optional role filter |
| `PATCH` | `/api/v1/auth/admin/users/{user_id}/status` | Access JWT (`ADMIN`) | Activate/deactivate/verify account (revokes all tokens on deactivation) |

## Standardized Error Envelope

All errors follow the API Gateway contract (`{"success": false, "error": {"code": ..., "message": ...}, "request_id": "..."}`):

- `VALIDATION_ERROR` (`422`)
- `INVALID_CREDENTIALS` (`401`)
- `EMAIL_ALREADY_EXISTS` (`409`)
- `UNAUTHORIZED` (`401`)
- `FORBIDDEN` (`403`)
- `TOKEN_EXPIRED` (`401`)
- `INVALID_TOKEN` (`401`)
- `TOKEN_REVOKED` (`401`)
- `USER_NOT_FOUND` (`404`)
- `INTERNAL_SERVER_ERROR` (`500`)

## Configuration

Copy `.env.example` to `.env` before running locally:

- `DATABASE_URL` — Async SQLAlchemy URL (`postgresql+asyncpg://...` in production/staging)
- `JWT_SECRET_KEY` — Minimum 32 bytes; production/staging reject placeholder or weak values at startup
- `ACCESS_TOKEN_EXPIRE_MINUTES` — Default `15`
- `REFRESH_TOKEN_EXPIRE_DAYS` — Default `7`
- `PASSWORD_HASHING_SCHEME` — `argon2`

## Database Migrations (Alembic)

```bash
alembic upgrade head
```

## Out-of-Band Admin Provisioning

Because `ADMIN` registration via public endpoints is strictly forbidden, provision an initial administrator out of band via CLI:

```bash
python -m app.core.bootstrap_admin --full-name "Platform Admin" --email "admin@codebridge.dev"
```

## Running Locally & Testing

```bash
# Run Identity Service on port 8001
uvicorn app.main:app --host 0.0.0.0 --port 8001

# Run full isolated test suite
pytest -v
```
