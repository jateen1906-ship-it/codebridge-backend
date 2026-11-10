# Review Service (`review-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8006`
- **Planned Database Ownership:** `codebridge_review`

## Responsibilities

- Task submissions
- Submission records
- Owner review
- Feedback
- Revision requests
- Approval workflow

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/review-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
