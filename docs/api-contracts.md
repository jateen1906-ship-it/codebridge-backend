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
- `Authorization: Bearer <token>` — Forwarded for authentication handling across `api-gateway` -> microservices (`identity-service`, `project-service`, etc.).

## 6. Implemented Domain Endpoints — Project Service (Phase 3)

Routed through API Gateway `/api/v1/projects` -> `project-service` (`:8002`) without stripping `/api/v1` (note `/api/v1/projects/{project_id}/github/...` retains higher routing priority for `github-service`):

| Method | Path | Role Required | Success Status | Key Error Codes |
| :--- | :--- | :--- | :---: | :--- |
| `POST` | `/api/v1/projects` | `OWNER` | `201 Created` | `UNAUTHORIZED`, `FORBIDDEN`, `VALIDATION_ERROR`, `IDENTITY_SERVICE_UNAVAILABLE` |
| `GET` | `/api/v1/projects` | Any authenticated (`OWNER`, `FREELANCER`, `ADMIN`) | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `VALIDATION_ERROR`, `IDENTITY_SERVICE_UNAVAILABLE` |
| `GET` | `/api/v1/projects/{project_id}` | Scoped (`OWNER` own, `FREELANCER` `OPEN`/assigned, `ADMIN` all) | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `IDENTITY_SERVICE_UNAVAILABLE` |
| `PATCH` | `/api/v1/projects/{project_id}` | `OWNER` (must own project, `OPEN` unassigned) | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `INVALID_PROJECT_STATUS`, `VALIDATION_ERROR` |
| `POST` | `/api/v1/projects/{project_id}/publish` | `OWNER` (must own project) | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `INVALID_PROJECT_STATUS`, `VALIDATION_ERROR` |
| `POST` | `/api/v1/projects/{project_id}/cancel` | `OWNER` (must own unassigned `OPEN` project) | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `INVALID_PROJECT_STATUS` |
| `POST` | `/api/v1/projects/{project_id}/select-freelancer` | `OWNER` (must own unassigned `OPEN` project) | `201 Created` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `FREELANCER_NOT_FOUND`, `INVALID_FREELANCER`, `PROJECT_ALREADY_ASSIGNED`, `INVALID_PROJECT_STATUS`, `CONCURRENT_ASSIGNMENT_CONFLICT`, `IDENTITY_SERVICE_UNAVAILABLE` |

## 7. Implemented Domain Endpoints — GitHub Service (Phase 4)

Routed through API Gateway `/api/v1/projects/{project_id}/github` *(priority `10`)* and `/api/v1/github` *(priority `100`)* -> `github-service` (`:8003`) without stripping `/api/v1`. Root operational probes `GET /health` and `GET /ready` are also exposed directly by `github-service`.

| Method | Path | Auth / Role Required | Success Status | Key Error Codes |
| :--- | :--- | :--- | :---: | :--- |
| `GET` | `/api/v1/github/oauth/authorize` | `OWNER` (must own `project_id`) | `302 Found` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `IDENTITY_SERVICE_UNAVAILABLE`, `PROJECT_SERVICE_UNAVAILABLE` |
| `GET` | `/api/v1/github/oauth/callback` | Hashed single-use `state` (optional `OWNER` match if Bearer sent) | `200 OK` / `302 Found` | `INVALID_OAUTH_CALLBACK`, `INVALID_OAUTH_STATE`, `OAUTH_STATE_EXPIRED`, `OAUTH_STATE_ALREADY_USED`, `GITHUB_OAUTH_DENIED`, `GITHUB_CONNECTION_ALREADY_EXISTS`, `GITHUB_OAUTH_FAILED`, `UPSTREAM_TIMEOUT` |
| `DELETE` | `/api/v1/github/connection` | `OWNER` | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `GITHUB_CONNECTION_NOT_FOUND`, `ACTIVE_ASSIGNMENT_CONFLICT`, `PROJECT_SERVICE_UNAVAILABLE` |
| `GET` | `/api/v1/github/repositories` | `OWNER` | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `GITHUB_CONNECTION_NOT_FOUND`, `VALIDATION_ERROR`, `GITHUB_RATE_LIMITED`, `GITHUB_TOKEN_INVALID`, `UPSTREAM_TIMEOUT` |
| `POST` | `/api/v1/projects/{project_id}/github/connect` | `OWNER` (must own project) | `201 Created` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `GITHUB_CONNECTION_NOT_FOUND`, `PROJECT_ALREADY_CONNECTED`, `REPOSITORY_MISMATCH`, `GITHUB_RESOURCE_NOT_FOUND`, `PROJECT_SERVICE_UNAVAILABLE` |
| `GET` | `/api/v1/projects/{project_id}/github/status` | Scoped via Project Service (`OWNER` own, `FREELANCER` `OPEN`/assigned, `ADMIN` all) | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `IDENTITY_SERVICE_UNAVAILABLE`, `PROJECT_SERVICE_UNAVAILABLE` |
| `DELETE` | `/api/v1/projects/{project_id}/github/disconnect` | `OWNER` (must own project) | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `PROJECT_REPOSITORY_NOT_FOUND`, `ACTIVE_ASSIGNMENT_CONFLICT`, `PROJECT_SERVICE_UNAVAILABLE` |
| `GET` | `/api/v1/projects/{project_id}/github/branches` | `OWNER` (must own project) | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `PROJECT_REPOSITORY_NOT_FOUND`, `GITHUB_RATE_LIMITED`, `GITHUB_TOKEN_INVALID`, `UPSTREAM_TIMEOUT` |
| `GET` | `/api/v1/projects/{project_id}/github/contents` | `OWNER` (must own project) | `200 OK` | `INVALID_PATH`, `INVALID_REF`, `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_REPOSITORY_NOT_FOUND`, `GITHUB_RESOURCE_NOT_FOUND`, `GITHUB_RATE_LIMITED`, `UPSTREAM_TIMEOUT` |
| `GET` | `/api/v1/projects/{project_id}/github/commits` | `OWNER` (must own project) | `200 OK` | `INVALID_PATH`, `INVALID_REF`, `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_REPOSITORY_NOT_FOUND`, `GITHUB_RATE_LIMITED`, `UPSTREAM_TIMEOUT` |

## 8. Implemented Domain Endpoints — Code Context Service (Phase 5)

Routed through API Gateway `/api/v1/code-context` -> `code-context-service` (`:8004`) without stripping `/api/v1`. Root operational probes `GET /health` and `GET /ready` are exposed directly by `code-context-service`.

| Method | Path | Auth / Role Required | Success Status | Key Error Codes |
| :--- | :--- | :--- | :---: | :--- |
| `POST` | `/api/v1/code-context/projects/{project_id}/index` | `OWNER` (must own project) | `201 Created` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `REPOSITORY_NOT_CONNECTED`, `INDEXING_IN_PROGRESS`, `INDEX_LIMIT_EXCEEDED`, `EMBEDDING_PROVIDER_UNAVAILABLE`, `GITHUB_SERVICE_UNAVAILABLE` |
| `POST` | `/api/v1/code-context/projects/{project_id}/reindex` | `OWNER` (must own project) | `200 OK` | Same as `/index` (preserves active index if build fails) |
| `GET` | `/api/v1/code-context/projects/{project_id}/index/status` | `OWNER` (own), actively assigned `FREELANCER`, `ADMIN` | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `INDEX_NOT_FOUND`, `PROJECT_SERVICE_UNAVAILABLE` |
| `POST` | `/api/v1/code-context/projects/{project_id}/query` | `OWNER` (own), actively assigned `FREELANCER` | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `INDEX_NOT_FOUND`, `VALIDATION_ERROR`, `EMBEDDING_PROVIDER_UNAVAILABLE`, `LLM_PROVIDER_UNAVAILABLE` |
| `POST` | `/api/v1/code-context/projects/{project_id}/insights/generate` | `OWNER` (must own project) | `201 Created` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `INDEX_NOT_FOUND`, `LLM_PROVIDER_UNAVAILABLE` |
| `GET` | `/api/v1/code-context/projects/{project_id}/insights` | `OWNER` (own), actively assigned `FREELANCER`, `ADMIN` | `200 OK` | `UNAUTHORIZED`, `FORBIDDEN`, `PROJECT_NOT_FOUND`, `INDEX_NOT_FOUND` |

## 9. Implemented Domain Endpoints — Phases 6–13

- **`workspace-service` (`/api/v1/workspace`, `:8005`):**
  - `POST /api/v1/workspace/projects/{project_id}`, `GET /api/v1/workspace/projects/{project_id}`, `GET /api/v1/workspace/{workspace_id}`, `POST /api/v1/workspace/{workspace_id}/{start,stop,refresh}`, `GET /api/v1/workspace/{workspace_id}/logs`, `DELETE /api/v1/workspace/{workspace_id}`, `POST /api/v1/workspace/cleanup-expired`
- **`review-service` (`/api/v1/submissions`, `/api/v1/review`, `:8006`):**
  - `POST /api/v1/submissions/projects/{project_id}`, `GET /api/v1/submissions/projects/{project_id}`, `GET /api/v1/submissions/{submission_id}`, `POST /api/v1/submissions/{submission_id}/{submit,withdraw,comments}`
  - `POST /api/v1/review/submissions/{submission_id}/{start,decision}`, `GET /api/v1/review/submissions/{submission_id}`, `GET /api/v1/review/projects/{project_id}`
- **`verification-service` (`/api/v1/verification`, `:8007`):**
  - `POST /api/v1/verification/records`, `GET /api/v1/verification/public/{public_verification_id}` *(unauthenticated, sanitized)*, `GET /api/v1/verification/projects/{project_id}`, `GET /api/v1/verification/records/{record_id}`, `POST /api/v1/verification/records/{record_id}/{verify,revoke}`
- **`payment-service` (`/api/v1/payment`, `:8008`):**
  - `POST /api/v1/payment/projects/{project_id}/escrow`, `POST /api/v1/payment/escrows/{escrow_id}/{fund,hold,release,refund,cancel}` *(supports `Idempotency-Key`)*, `GET /api/v1/payment/projects/{project_id}`, `GET /api/v1/payment/escrows/{escrow_id}`, `GET /api/v1/payment/escrows/{escrow_id}/transactions`
- **`notification-service` (`/api/v1/notifications`, `:8009`):**
  - `POST /api/v1/notifications/events` *(`X-Service-Key` or `ADMIN`)*, `GET /api/v1/notifications`, `POST /api/v1/notifications/{notification_id}/read`, `POST /api/v1/notifications/read-all`, `GET/PUT /api/v1/notifications/preferences`
- **`admin-service` (`/api/v1/admin`, `:8010`, strictly `ADMIN`):**
  - `GET /api/v1/admin/users`, `POST /api/v1/admin/users/{user_id}/{suspend,restore}`, `GET /api/v1/admin/projects`, `GET /api/v1/admin/audit-logs`, `POST/GET /api/v1/admin/reports`, `POST /api/v1/admin/reports/{report_id}/resolve`, `GET /api/v1/admin/actions`
- **`audit-service` (`/api/v1/audit`, `:8011`, append-only):**
  - `POST /api/v1/audit/events` *(`X-Service-Key` or `ADMIN`)*, `GET /api/v1/audit/events`, `GET /api/v1/audit/projects/{project_id}`
- **`observability-service` (`/api/v1/observability`, `:8012`):**
  - `GET /api/v1/observability/services` *(`ADMIN`)*, `POST /api/v1/observability/telemetry` *(`X-Service-Key` or `ADMIN`)*, `GET /api/v1/observability/metrics` *(`ADMIN`)*, `GET /api/v1/observability/errors` *(`ADMIN`)*


