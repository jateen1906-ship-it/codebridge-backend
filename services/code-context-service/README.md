# Code Context Service (`code-context-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8004`
- **Planned Database Ownership:** `codebridge_code_context`

## Responsibilities

- Repository analysis
- Code understanding
- RAG
- AI code insights
- Documentation retrieval
- Repository health reports

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/code-context-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
