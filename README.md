# CodeBridge Backend

Monorepo for the **CodeBridge** software project completion and developer collaboration platform backend.

> **Project Status — Phase 0 (Repository Initialization)**
> This repository currently contains only the initialized microservices directory structure, isolation scaffolding, configuration placeholders, and architecture documentation. No services, APIs, databases, or integrations are implemented or running yet.

## Planned Architecture & Tech Stack

- **Language & Framework:** Python, FastAPI
- **Architecture:** 12 independent domain microservices + 1 API Gateway
- **Inter-Service Communication:** Synchronous HTTP/REST using HTTPX (no RabbitMQ, Kafka, or event-driven message brokers)
- **Database Strategy:** PostgreSQL with strict database-per-service ownership, SQLAlchemy ORM, and Alembic migrations (planned)
- **Orchestration & Testing:** Docker, Docker Compose, Pytest

## Services & Planned Local Ports

| Component | Role | Planned Local Port | Directory |
| :--- | :--- | :---: | :--- |
| `api-gateway` | Single public API entry point | `8000` | `api-gateway/` |
| `identity-service` | Auth, users, profiles, RBAC | `8001` | `services/identity-service/` |
| `project-service` | Projects, requirements, tasks, lifecycle | `8002` | `services/project-service/` |
| `github-service` | GitHub OAuth, repo connections, commits | `8003` | `services/github-service/` |
| `code-context-service` | Repo analysis, RAG, AI code insights | `8004` | `services/code-context-service/` |
| `workspace-service` | Isolated coding workspaces lifecycle | `8005` | `services/workspace-service/` |
| `review-service` | Submissions, owner reviews, approvals | `8006` | `services/review-service/` |
| `verification-service` | Automated & acceptance testing | `8007` | `services/verification-service/` |
| `payment-service` | Simulated escrow, holds, releases, refunds | `8008` | `services/payment-service/` |
| `notification-service` | Notifications, announcements, history | `8009` | `services/notification-service/` |
| `admin-service` | User & project moderation, reports | `8010` | `services/admin-service/` |
| `audit-service` | Activity logs and audit history | `8011` | `services/audit-service/` |
| `observability-service` | Health, readiness, metrics, monitoring | `8012` | `services/observability-service/` |

## Repository Structure

```text
codebridge-backend/
├── api-gateway/        # Single public entry point (FastAPI)
├── services/           # 12 independent domain microservices
├── shared/             # Future shared HTTP contracts, schemas, exceptions, utilities
├── infrastructure/     # Future Docker, PostgreSQL, Redis, and monitoring configs
├── docs/               # Architecture, service, communication, DB, and API contract docs
├── tests/              # Future cross-service integration / end-to-end test suite
├── .github/            # Future CI/CD workflows and repository templates
├── docker-compose.yml  # Future local multi-service orchestration placeholder
├── .env.example        # Root environment & service URL placeholders
├── .gitignore
├── LICENSE
└── README.md
```

## Service Isolation Principles

1. Every service is independently runnable and deployable, with its own `app/`, `tests/`, `requirements.txt`, `.env.example`, `Dockerfile`, and `README.md`.
2. Services do **not** share a single `requirements.txt`, database connection, or FastAPI application instance.
3. Services must **never** directly import another service's internal Python modules (models, repositories, or business logic).
4. All cross-service collaboration happens over synchronous HTTP/REST APIs.

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Service Responsibilities](docs/services.md)
- [Inter-Service Communication](docs/communication.md)
- [Database Design](docs/database-design.md)
- [API Contracts & Conventions](docs/api-contracts.md)

## License

Licensed under the [MIT License](LICENSE).
