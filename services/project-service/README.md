# Project Service (`project-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8002`
- **Planned Database Ownership:** `codebridge_project`

## Responsibilities

- Project creation and management
- Project requirements
- Tasks
- Freelancer assignments
- Deadlines
- Project lifecycle

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/project-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
