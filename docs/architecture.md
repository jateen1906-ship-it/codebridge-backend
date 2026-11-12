# CodeBridge Backend Architecture

## 1. Overview

The **CodeBridge** backend is structured as a **monorepo** housing an independent **microservices architecture** built with Python and FastAPI. The platform comprises **12 independent domain microservices** fronted by **1 stateless API Gateway** (`api-gateway`).

All components live in one repository for coordinated versioning, documentation, and testing while remaining **independently runnable, testable, and deployable** with strict database-per-service isolation.

## 2. Technology Stack

| Concern | Technology |
| :--- | :--- |
| Language & Web Framework | Python 3.11+ / 3.13, FastAPI, Uvicorn |
| Service-to-Service Transport | Synchronous HTTP/REST via `httpx.AsyncClient` |
| Database | PostgreSQL 16 (database-per-service) |
| ORM & Schema Migrations | SQLAlchemy 2.x Async, Alembic (per-service) |
| Vector Store & RAG Chunking | ChromaDB, LangChain text splitters |
| Containerization & Orchestration | Docker, Docker Compose |
| Testing | Pytest, `pytest-asyncio`, `aiosqlite` |

### Explicit Architectural Boundaries

- **No Event-Driven Message Brokers:** RabbitMQ, Kafka, and asynchronous message buses are **not** used. All communication across service boundaries is synchronous HTTP/REST.
- **No Shared Monolithic Runtime:** Each service owns its own FastAPI `app`, dependencies, and database engine.
- **Zero Cross-Service Internal Imports:** Services communicate strictly over HTTPX REST clients in `app/clients/`.

## 3. Monorepo Layout

```text
codebridge-backend/
├── api-gateway/                  # Single public entry point (port 8000)
├── services/                     # 12 isolated microservices (ports 8001–8012)
├── shared/                       # Transport-level documentation & contracts
├── infrastructure/postgres/      # PostgreSQL init-databases.sql for 12 DBs
├── docs/                         # System architecture, service, DB, and API contracts
├── tests/                        # Live multi-process 20-step E2E platform suite
├── docker-compose.yml            # PostgreSQL + API Gateway + 12 microservices
├── .env.example                  # Root environment variable template
└── README.md
```

## 4. The API Gateway (`8000`)

`api-gateway` is a stateless edge proxy that provides:

1. **Public Prefix Routing:** Routes `/api/v1/...` traffic to all 12 microservices without stripping `/api/v1`, prioritizing `/api/v1/projects/{project_id}/github` (`github-service`) over `/api/v1/projects` (`project-service`).
2. **CORS Enforcement:** Centralized allow-origin / allow-methods / allow-headers including `Authorization`, `Idempotency-Key`, and `X-Request-ID`.
3. **Request Correlation (`X-Request-ID`):** Validates incoming UUIDv4 correlation IDs or generates fresh ones, forwarding them downstream and echoing them back in responses.
4. **Sanitized Error Envelopes:** Translates downstream `5xx`, timeouts (`504`), and unavailable peers (`503`) cleanly without leaking internal host URLs or stack traces.
5. **Path-Traversal Guardrails & Safe Header Filtering:** Rejects `..` / null bytes before forwarding and filters hop-by-hop headers.

```text
[ Frontend / Clients ]
          │
          ▼  HTTPS / REST (Public API)
┌────────────────────┐
│    api-gateway     │  (Port 8000 — Routing, CORS, Correlation ID, Error Envelope)
└─────────┬──────────┘
          │  Synchronous HTTP / REST (HTTPX)
          ├──► identity-service       (8001)
          ├──► project-service        (8002)
          ├──► github-service         (8003)
          ├──► code-context-service   (8004)
          ├──► workspace-service      (8005)
          ├──► review-service         (8006)
          ├──► verification-service   (8007)
          ├──► payment-service        (8008)
          ├──► notification-service   (8009)
          ├──► admin-service          (8010)
          ├──► audit-service          (8011)
          └──► observability-service  (8012)
```
