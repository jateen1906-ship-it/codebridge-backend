# API Contracts & Conventions

> **Phase 1 Note:** API Gateway health, correlation ID, routing, and standardized error envelopes are implemented in `api-gateway/`. Domain microservice endpoints remain to be implemented in subsequent phases.

## 1. API Versioning Conventions

- **URL Path Versioning:** All public and internal domain APIs use major-version path prefixing:
  - Public API Gateway routes: `/api/v1/<domain-resource>/...`
  - Internal microservice routes: `/api/v1/<resource>/...`
- **Operational Endpoints (Unversioned):** Every microservice and the API Gateway expose standard operational endpoints at root:
  - `GET /health` — Liveness/self-health check (`api-gateway` returns `{"status": "healthy", "service": "api-gateway", "version": "1.0.0"}`)

## 2. Endpoint Naming Conventions

- Use **plural or domain nouns** in **lowercase kebab-case** for resource groups (e.g., `/api/v1/projects`, `/api/v1/freelancers`, `/api/v1/code-context`).
- Project-scoped GitHub integration paths (`/api/v1/projects/{project_id}/github/...`) take routing priority over generic `/api/v1/projects/...` paths at the API Gateway.
- Use path parameters for resource identifiers (`/api/v1/projects/{project_id}`) and query parameters for filtering, sorting, and pagination (`?status=active&page=1&page_size=20`).

### API Gateway Route Prefixes (Implemented in `ServiceRegistry`)

| Gateway Prefix (`/api/v1/...`) | Downstream Service | Port |
| :--- | :--- | :---: |
| `/auth`, `/freelancers`, `/profile` | `identity-service` | `8001` |
| `/projects/{project_id}/github` *(priority)*, `/github` | `github-service` | `8003` |
| `/projects` | `project-service` | `8002` |
| `/code-context` | `code-context-service` | `8004` |
| `/workspace` | `workspace-service` | `8005` |
| `/submissions`, `/review` | `review-service` | `8006` |
| `/verification` | `verification-service` | `8007` |
| `/payment` | `payment-service` | `8008` |
| `/notifications` | `notification-service` | `8009` |
| `/admin` | `admin-service` | `8010` |
| `/audit` | `audit-service` | `8011` |
| `/observability` | `observability-service` | `8012` |

## 3. HTTP Methods & Status Codes

| Method | Semantics | Typical Success Status |
| :--- | :--- | :--- |
| `GET` | Retrieve a resource or collection (safe, idempotent) | `200 OK` |
| `POST` | Create a new resource or trigger a non-idempotent workflow action | `201 Created`, `200 OK`, or `202 Accepted` |
| `PUT` | Full replacement of an existing resource | `200 OK` |
| `PATCH` | Partial update of an existing resource | `200 OK` |
| `DELETE` | Remove or archive a resource | `204 No Content` or `200 OK` |

### Standard Error Status Codes

- `400 Bad Request` — Malformed request or domain rule violation (`VALIDATION_ERROR`)
- `401 Unauthorized` — Missing or invalid authentication credentials (`UNAUTHORIZED`)
- `403 Forbidden` — Authenticated actor lacks required role/permission (`FORBIDDEN`)
- `404 Not Found` — Target resource or route does not exist (`NOT_FOUND`)
- `409 Conflict` — Resource state conflict or duplicate identifier (`CONFLICT`)
- `422 Unprocessable Entity` — Request schema validation failure (`VALIDATION_ERROR`)
- `500 Internal Server Error` — Unhandled service error (`INTERNAL_SERVER_ERROR`)
- `502 Bad Gateway` — Invalid or failing upstream microservice response (`INTERNAL_SERVER_ERROR` / `SERVICE_UNAVAILABLE`)
- `503 Service Unavailable` — Downstream service unreachable or unconfigured (`SERVICE_UNAVAILABLE`)
- `504 Gateway Timeout` — Downstream service exceeded synchronous timeout budget (`SERVICE_TIMEOUT`)

## 4. Common Response Structures

### Standard Error Response Envelope

All Gateway error responses and normalized downstream errors follow this structure:

```json
{
  "success": false,
  "error": {
    "code": "SERVICE_UNAVAILABLE",
    "message": "The requested service is currently unavailable."
  },
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

## 5. Standard Headers

- `X-Request-ID` — Validated canonical UUIDv4 request trace ID generated at `api-gateway` if missing or invalid, attached to request state, propagated downstream over HTTPX, logged in structured JSON logs, and returned in every response.
- `Content-Type: application/json` — Standard payload content type.
- `Authorization: Bearer <token>` — Forwarded for future authentication handling at `identity-service` / `api-gateway`.
