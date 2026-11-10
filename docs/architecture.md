# CodeBridge Backend Architecture

> **Phase 0 Note:** This document describes the target architecture initialized in Phase 0. Implementation of services, databases, authentication, and integrations begins in subsequent phases.

## 1. Overview

The **CodeBridge** backend is structured as a **monorepo** housing a genuine **microservices architecture** built with Python and FastAPI. The platform comprises **12 independent domain microservices** fronted by **1 API Gateway** (`api-gateway`).

While all components live in a single GitHub repository for coordinated versioning, documentation, and CI/CD, every service is architected to be **independently runnable, testable, and deployable**.

## 2. Technology Stack (Planned)

| Concern | Planned Technology |
| :--- | :--- |
| Language & Web Framework | Python, FastAPI |
| Service-to-Service Transport | Synchronous HTTP/REST via HTTPX |
| Database | PostgreSQL (database-per-service) |
| ORM & Schema Migrations | SQLAlchemy, Alembic |
| Containerization & Orchestration | Docker, Docker Compose |
| Testing | Pytest |

### Explicit Non-Goals in This Architecture

- **No Event-Driven Message Brokers:** RabbitMQ, Kafka, and asynchronous message buses are **not** used. All communication across service boundaries is synchronous HTTP/REST.
- **No Shared Monolithic Runtime:** There is no shared FastAPI application importing routers from multiple domains.

## 3. Monorepo Layout

```text
codebridge-backend/
├── api-gateway/        # Single public entry point for clients (port 8000)
├── services/           # 12 isolated business & platform microservices (ports 8001–8012)
├── shared/             # Future common HTTP contracts, schemas, exceptions, utilities
├── infrastructure/     # Future Docker, PostgreSQL, Redis, and monitoring definitions
├── docs/               # System architecture and design documentation
├── tests/              # Future cross-service integration and E2E tests
├── .github/            # Future CI/CD workflows
├── docker-compose.yml  # Future container orchestration definition
├── .env.example        # Root environment placeholder template
└── README.md
```

## 4. The API Gateway Pattern

`api-gateway` (planned port `8000`) is **not** a business microservice. It acts as the **single public entry point** for frontend applications and external API consumers.

### Responsibilities of `api-gateway`

1. **Public Routing & Forwarding:** Translates public `/api/v1/...` client requests into internal HTTP calls to the responsible downstream microservice.
2. **CORS Policy Enforcement:** Manages cross-origin rules centrally for frontend applications.
3. **Request Correlation IDs:** Validates or generates a canonical UUIDv4 `X-Request-ID` header on every incoming request, binds it to request state and structured logs, and propagates it downstream and in responses.
4. **Centralized Error Handling:** Normalizes downstream HTTP failures, timeouts, and unavailability into a consistent public error envelope.
5. **Configurable Service Discovery:** Resolves internal microservice base URLs strictly through environment configuration rather than hardcoded endpoints.
6. **Future Authentication Verification:** In a later phase, verifies bearer tokens with `identity-service` (or validated token metadata) before forwarding protected requests and injects verified identity context headers downstream.

```text
[ Frontend / Clients ]
          │
          ▼  HTTPS / REST (Public API)
┌────────────────────┐
│    api-gateway     │  (Port 8000 — Routing, CORS, Correlation ID, Auth Check)
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

## 5. Service Isolation & Coupling Rules

Every component under `services/` and `api-gateway/` enforces strict boundaries:

- **Independent Packaging:** Each service maintains its own `requirements.txt`, `Dockerfile`, `.env.example`, `README.md`, `app/`, and `tests/`. There is no single global `requirements.txt`.
- **No Cross-Service Internal Imports:** A service must never import `models`, `repositories`, `services`, or `core` modules from another service's Python package.
- **Database-Per-Service:** No two services share a database connection or SQLAlchemy session.
- **Controlled `shared/` Usage:** The root `shared/` directory (`contracts/`, `schemas/`, `exceptions/`, `utilities/`) is reserved strictly for transport-level HTTP contracts, standardized error formats, and stateless helpers — never domain models or database logic.

