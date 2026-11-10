# Observability Service (`observability-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8012`
- **Planned Database Ownership:** `codebridge_observability (optional/telemetry)`

## Responsibilities

- Health monitoring
- Readiness monitoring
- Service metrics
- Operational monitoring

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/observability-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
