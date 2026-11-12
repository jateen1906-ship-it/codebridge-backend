# Inter-Service Communication Design

## 1. Synchronous HTTP/REST Model

All communication between `api-gateway` and downstream microservices, as well as every service-to-service interaction, uses **synchronous HTTP/REST over JSON** via **`httpx.AsyncClient`** encapsulated inside each service's `app/clients/` package. Message brokers (RabbitMQ, Kafka, Redis Pub/Sub) are intentionally absent.

## 2. Standard Headers & Authentication

- **Correlation Tracing:** `X-Request-ID` (canonical UUIDv4) is generated/validated at `api-gateway` and propagated across every downstream HTTPX request.
- **User-Delegated Calls:** Primary domain calls forward caller `Authorization: Bearer <token>` so `identity-service` and `project-service` enforce real identity and ownership/assignment RBAC at every hop.
- **Internal Service Intake:** `notification-service` (`POST /api/v1/notifications/events`), `audit-service` (`POST /api/v1/audit/events`), and `observability-service` (`POST /api/v1/observability/telemetry`) authenticate internal producers via constant-time `X-Service-Key` (`INTERNAL_SERVICE_KEY`) comparison (or `ADMIN` Bearer JWT).

## 3. Fail-Closed vs. Fail-Soft Fault Semantics

- **Critical Authorization & Precondition Hops (Fail Closed):**
  - Identity verification (`identity-service`) fails closed (`401`/`403`/`503`).
  - `review-service` -> `project-service` approval transition sync fails closed (`503`) before committing `APPROVED` state, or maintains atomic consistency.
  - `verification-service` -> `review-service` (`approved` check) fails closed (`409`/`503`).
  - `payment-service` -> `review-service` (`/release` approval gate) and `project-service` (budget check) fail closed (`409`/`503`).
  - `admin-service` -> `identity-service` user suspension/restoration fails closed (`503`).
- **Telemetry Side-Effect Hops (Fail Soft):**
  - Notification and Audit emission (`notification-service`, `audit-service`) from `review`, `verification`, `payment`, and `admin` services execute with short bounded timeouts and soft error logging so a secondary telemetry outage cannot roll back a committed primary financial/review transaction.

## 4. Timeout & Resilience Rules

- Service-to-service calls enforce explicit bounded connect/total timeouts (`SERVICE_CLIENT_TIMEOUT_SECONDS`, default `10.0s`; `OBSERVABILITY_PROBE_TIMEOUT_SECONDS`, default `3.0s`).
- State-mutating escrow endpoints (`/escrow`, `/fund`, `/release`, `/refund`) enforce SHA-256 request-hash bound `Idempotency-Key` checks to reject mismatched replays (`409`) and safely return identical responses on retries.
