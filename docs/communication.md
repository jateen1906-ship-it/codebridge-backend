# Inter-Service Communication Design

> **Phase 0 Note:** This document defines the communication protocol, client conventions, timeout standards, and error propagation rules to be implemented in later phases.

## 1. Synchronous HTTP/REST Model

All communication between:
- `api-gateway` and downstream microservices, and
- any microservice and a peer microservice

uses **synchronous HTTP/REST over JSON**.

CodeBridge explicitly **does not** use RabbitMQ, Apache Kafka, Redis Pub/Sub event buses, or an event-driven architecture. Keeping all cross-service interactions as explicit HTTP/REST calls preserves simple request tracing, clear failure semantics, and straightforward local debugging.

## 2. Planned HTTP Client: HTTPX

Every component that makes outbound internal HTTP calls (`api-gateway/app/clients/` and `services/<name>/app/clients/`) will use **HTTPX** (`httpx.AsyncClient`) as the standard client library.

### Client Design Principles (Planned)

- **Dedicated Client Modules:** Outbound HTTP calls must be encapsulated inside each service's `app/clients/` package rather than scattered across route handlers or domain services.
- **Connection Pooling:** Reuse `httpx.AsyncClient` lifecycle per FastAPI application lifespan rather than instantiating a new client per request.
- **Header Propagation:** Automatically forward `X-Request-ID` (and when applicable, verified user context headers) on every internal request.

## 3. Service URL Configuration

Internal service addresses must **never** be hardcoded. Every caller resolves target base URLs through environment variables defined in `.env.example` / `.env`.

### Planned Local Development Ports

| Environment Variable | Planned Local URL | Target Component |
| :--- | :--- | :--- |
| `API_GATEWAY_URL` | `http://localhost:8000` | `api-gateway` |
| `IDENTITY_SERVICE_URL` | `http://localhost:8001` | `identity-service` |
| `PROJECT_SERVICE_URL` | `http://localhost:8002` | `project-service` |
| `GITHUB_SERVICE_URL` | `http://localhost:8003` | `github-service` |
| `CODE_CONTEXT_SERVICE_URL` | `http://localhost:8004` | `code-context-service` |
| `WORKSPACE_SERVICE_URL` | `http://localhost:8005` | `workspace-service` |
| `REVIEW_SERVICE_URL` | `http://localhost:8006` | `review-service` |
| `VERIFICATION_SERVICE_URL` | `http://localhost:8007` | `verification-service` |
| `PAYMENT_SERVICE_URL` | `http://localhost:8008` | `payment-service` |
| `NOTIFICATION_SERVICE_URL` | `http://localhost:8009` | `notification-service` |
| `ADMIN_SERVICE_URL` | `http://localhost:8010` | `admin-service` |
| `AUDIT_SERVICE_URL` | `http://localhost:8011` | `audit-service` |
| `OBSERVABILITY_SERVICE_URL` | `http://localhost:8012` | `observability-service` |

In containerized deployments (future Docker Compose / orchestrator), these URLs resolve via service DNS names (e.g., `http://identity-service:8001`) using the exact same environment variables.

## 4. Timeout Standards

Synchronous chains require strict, bounded timeouts so one slow service cannot exhaust worker concurrency across the platform.

- **Default Inter-Service Timeout:** `10.0` seconds total (`connect=2.0s`, `read=8.0s`, `write=5.0s`, `pool=2.0s`), configurable via `HTTP_CLIENT_TIMEOUT_SECONDS`.
- **Gateway Timeout Budget:** The `api-gateway` timeout must be slightly higher than (or explicitly budgeted against) single-hop service timeouts when orchestrating downstream requests.
- **Long-Running Operations:** Operations such as repository analysis (`code-context-service`), workspace provisioning (`workspace-service`), or test execution (`verification-service`) should return an accepted resource/job reference (`202 Accepted` or status-tracked resource) and expose a status-polling endpoint rather than holding an HTTP connection open indefinitely.

## 5. Error Propagation & Fault Handling

Because calls are synchronous, upstream callers must handle downstream failures predictably:

1. **4xx Client Errors from Downstream:**
   - Validation, authentication, authorization, conflict, or not-found errors (`400`, `401`, `403`, `404`, `409`, `422`) returned by a microservice are surfaced cleanly by `api-gateway` (preserving the HTTP status code, standardized error envelope, and `X-Request-ID`).
2. **5xx Upstream Failures:**
   - Downstream `500`/`502`/`503` responses are logged with correlation metadata and surfaced to clients via `api-gateway` as `502 Bad Gateway` or `503 Service Unavailable` without leaking internal stack traces.
3. **Timeouts (`httpx.TimeoutException`):**
   - Translated to `504 Gateway Timeout` at the gateway or handled via domain-specific fallback/failure state in service-to-service calls.
4. **Connection / DNS Errors (`httpx.ConnectError`):**
   - Translated to `503 Service Unavailable`.
5. **Retry Discipline:**
   - Automatic retries must **only** be considered for strictly idempotent `GET` requests on transient network errors, never blindly on state-mutating `POST`/`PATCH`/`DELETE` calls unless an explicit idempotency key is implemented.

## 6. Implemented Phase 4 Synchronous Integrations — `github-service`

- **`github-service` -> `identity-service` (`GET /api/v1/auth/me`):** Synchronous token verification forwarding caller `Authorization` header and `X-Request-ID` via pooled `IdentityClient`; fails closed with `503 IDENTITY_SERVICE_UNAVAILABLE`.
- **`github-service` -> `project-service` (`GET /api/v1/projects/{project_id}`):** Synchronous project ownership, read-visibility authorization, and active-workflow verification forwarding caller `Authorization` header and `X-Request-ID` via pooled `ProjectClient`; fails closed with `503 PROJECT_SERVICE_UNAVAILABLE`.
- **`github-service` -> GitHub (`https://github.com/login/oauth/access_token`, `https://api.github.com`):** Fixed-host OAuth token exchange and bounded read-only REST API client (`GitHubHTTPClient`, `follow_redirects=False`) with timeout (`504`) and rate-limit (`429`) translation.


