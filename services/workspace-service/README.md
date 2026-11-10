# Workspace Service (`workspace-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8005`
- **Planned Database Ownership:** `codebridge_workspace`

## Responsibilities

- Isolated coding workspaces
- Workspace creation
- Workspace lifecycle
- Start and stop operations
- Docker integration in a future phase

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/workspace-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
