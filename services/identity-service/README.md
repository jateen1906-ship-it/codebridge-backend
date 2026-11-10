# Identity Service (`identity-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8001`
- **Planned Database Ownership:** `codebridge_identity`

## Responsibilities

- User identity
- User profiles
- Freelancer profiles
- Authentication
- Role-based access control
- Owner, freelancer, and admin identities

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/identity-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
