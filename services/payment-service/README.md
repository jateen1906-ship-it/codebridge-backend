# Payment Service (`payment-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8008`
- **Planned Database Ownership:** `codebridge_payment`

## Responsibilities

- Simulated escrow
- Payment authorization
- Payment hold
- Payment release
- Refund simulation
- Payment audit records

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/payment-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
