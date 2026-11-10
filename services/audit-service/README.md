# Audit Service (`audit-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8011`
- **Planned Database Ownership:** `codebridge_audit`

## Responsibilities

- Activity logs
- User activity
- Project activity
- Workspace access records
- Audit history

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/audit-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
