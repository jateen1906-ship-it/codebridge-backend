# Verification Service (`verification-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8007`
- **Planned Database Ownership:** `codebridge_verification`

## Responsibilities

- Automated tests
- Acceptance testing
- Code validation
- Test results
- Verification reports

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/verification-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
