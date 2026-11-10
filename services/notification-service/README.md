# Notification Service (`notification-service`)

> **Status:** Phase 0 initialized (no business logic, database connections, or endpoints implemented yet).

- **Planned Local Port:** `8009`
- **Planned Database Ownership:** `codebridge_notification`

## Responsibilities

- User notifications
- System announcements
- Notification history
- Future WebSocket support

## Service Isolation Rules

- Independently runnable and deployable FastAPI service (`services/notification-service`).
- Owns its own `requirements.txt`, `.env.example`, `Dockerfile`, `app/`, and `tests/`.
- Communicates with peer microservices strictly via synchronous HTTP/REST (HTTPX).
- Must not import internal models, repositories, or core modules from any other microservice.
