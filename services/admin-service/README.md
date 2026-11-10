# Admin Service (`admin-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8010`
- **Planned Database Ownership:** `codebridge_admin`

## Responsibilities

- User moderation
- Project moderation
- Reports
- Administrative operations

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/admin-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
