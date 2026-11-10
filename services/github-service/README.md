# GitHub Service (`github-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8003`
- **Planned Database Ownership:** `codebridge_github`

## Responsibilities

- GitHub OAuth integration
- Repository connections
- Repository metadata
- Commits and repository access

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/github-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
