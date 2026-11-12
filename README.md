# CodeBridge Backend

Monorepo for the **CodeBridge** software project completion and developer collaboration platform backend.

> **Project Status — Complete Microservices Implementation (Phases 1–20)**
> All 13 services (**1 API Gateway** + **12 independent domain microservices**) are implemented with Pydantic v2 schemas, FastAPI routers, synchronous HTTPX cross-service clients, isolated SQLAlchemy 2.x async persistence, Alembic migrations, Dockerfiles, root Docker Compose orchestration, per-service Pytest suites, and a multi-process end-to-end platform lifecycle suite.

## Architecture & Tech Stack

- **Language & Framework:** Python 3.11+ / 3.13, FastAPI, Uvicorn
- **Architecture:** 12 independent domain microservices + 1 API Gateway (`8000`–`8012`)
- **Inter-Service Communication:** Synchronous HTTP/REST via `httpx.AsyncClient` (zero event brokers / RabbitMQ / Kafka)
- **Database Strategy:** PostgreSQL with strict database-per-service ownership (`codebridge_identity` through `codebridge_observability`), async SQLAlchemy 2.x, and per-service Alembic migrations
- **Security Controls:** Argon2 passwords, HS256 JWT access/refresh rotation, AES-256-GCM encrypted GitHub OAuth credentials, zip-slip / decompression-bomb hardened workspace snapshots, canonical SHA-256 + HMAC-SHA256 verification proofs, idempotent simulated escrow holds/releases, constant-time `X-Service-Key` internal intake, and secret-scrubbing append-only audit logs
- **Orchestration & Testing:** Docker, Docker Compose, Pytest, `pytest-asyncio`

## Implemented Services & Ports

| Component | Role | Port | Owned Database | Directory |
| :--- | :--- | :---: | :--- | :--- |
| `api-gateway` | Single public entry point & proxy | `8000` | *(Stateless)* | [api-gateway/](api-gateway/) |
| `identity-service` | Auth, JWT rotation, users, profiles, RBAC | `8001` | `codebridge_identity` | [services/identity-service/](services/identity-service/) |
| `project-service` | Projects, requirements, assignments, lifecycle | `8002` | `codebridge_project` | [services/project-service/](services/project-service/) |
| `github-service` | GitHub OAuth App, AES-256 tokens, repo snapshots | `8003` | `codebridge_github` | [services/github-service/](services/github-service/) |
| `code-context-service` | Repo indexing, ChromaDB vectors, grounded RAG & insights | `8004` | `codebridge_code_context` | [services/code-context-service/](services/code-context-service/) |
| `workspace-service` | Isolated workspaces, zip extraction, Docker/mock lifecycle | `8005` | `codebridge_workspace` | [services/workspace-service/](services/workspace-service/) |
| `review-service` | Submissions, comments, owner review decisions | `8006` | `codebridge_review` | [services/review-service/](services/review-service/) |
| `verification-service` | HMAC-SHA256 work proofs, public lookup, revocation | `8007` | `codebridge_verification` | [services/verification-service/](services/verification-service/) |
| `payment-service` | Idempotent simulated escrow accounts, holds, releases | `8008` | `codebridge_payment` | [services/payment-service/](services/payment-service/) |
| `notification-service` | In-app inbox, email delivery logs, user preferences | `8009` | `codebridge_notification` | [services/notification-service/](services/notification-service/) |
| `admin-service` | ADMIN-only moderation, suspension/restoration, inspection | `8010` | `codebridge_admin` | [services/admin-service/](services/admin-service/) |
| `audit-service` | Append-only scrubbed compliance & activity audit trail | `8011` | `codebridge_audit` | [services/audit-service/](services/audit-service/) |
| `observability-service` | Concurrent `/health` & `/ready` probing, metrics, errors | `8012` | `codebridge_observability` | [services/observability-service/](services/observability-service/) |

## Repository Structure

```text
codebridge-backend/
├── api-gateway/                  # Edge API Gateway (port 8000)
├── services/                     # 12 isolated microservices (ports 8001–8012)
├── shared/                       # Shared transport conventions & docs
├── infrastructure/postgres/      # PostgreSQL multi-DB init script (init-databases.sql)
├── docs/                         # Architecture, service, communication, DB, & API contracts
├── tests/e2e_platform_test.py    # Live multi-process 13-service 20-step E2E test
├── docker-compose.yml            # Full PostgreSQL + Gateway + 12-service stack
├── .env.example                  # Root environment configuration template
└── README.md
```

## Service Isolation Principles

1. Every service is independently runnable and deployable, with its own `app/`, `migrations/`, `tests/`, `requirements.txt`, `.env.example`, `Dockerfile`, and `README.md`.
2. Services do **not** share a single `requirements.txt`, database connection, or FastAPI application instance.
3. Services must **never** directly import another service's internal Python modules (`models`, `repositories`, `services`, or `core`).
4. All cross-service collaboration happens strictly over synchronous HTTP/REST (`httpx.AsyncClient`).

## Running Containerized Stack

```bash
cp .env.example .env
docker compose up --build
```

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Service Responsibilities](docs/services.md)
- [Inter-Service Communication](docs/communication.md)
- [Database Design](docs/database-design.md)
- [API Contracts & Conventions](docs/api-contracts.md)

## License

Licensed under the [MIT License](LICENSE).
