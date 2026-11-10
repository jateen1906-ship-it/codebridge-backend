# CodeBridge API Gateway (`api-gateway`)

> **Status:** Phase 1 implemented — standalone FastAPI API Gateway foundation (downstream microservices are not implemented yet).

The **API Gateway** is the single public HTTP entry point for frontend applications in the CodeBridge platform. It is **not** a business microservice; it enforces cross-cutting edge concerns and forwards requests synchronously over HTTP/REST to the 12 domain microservices.

## Responsibilities

- FastAPI application initialization with lifespan-managed asynchronous HTTPX client pooling
- Environment-driven configuration and startup validation via Pydantic Settings
- Centralized service registry with explicit public-to-internal route mappings
- Synchronous HTTP/REST request forwarding (preserving methods, query params, bodies, and safe response content types)
- Request correlation ID middleware (`X-Request-ID` canonical UUID validation, propagation, and response header injection)
- Standardized JSON error handling (`VALIDATION_ERROR`, `NOT_FOUND`, `SERVICE_TIMEOUT`, `SERVICE_UNAVAILABLE`, `INTERNAL_SERVER_ERROR`) without exposing stack traces or internal hostnames
- Environment-configurable CORS policy
- Self-health endpoint (`GET /health`) independent of downstream microservice availability
- Structured JSON logging without logging secrets, authorization headers, or request bodies

## Architecture

```text
Client / Frontend
       │
       ▼  HTTP/REST (/health, /api/v1/...)
┌──────────────────────────────────────────────────────────┐
│                     api-gateway (8000)                   │
│  1. CorrelationIDMiddleware (X-Request-ID UUID)          │
│  2. CORSMiddleware (explicit env origins)                │
│  3. Exception Handlers (standard JSON error envelope)    │
│  4. ServiceRegistry + GatewayHTTPClient (single pool)    │
└──────┬───────────────────────────────────────────────────┘
       │  Synchronous HTTP/REST via HTTPX
       ▼
  12 Independent Microservices (Ports 8001–8012)
```

## Folder Structure

```text
api-gateway/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory, lifespan, middleware & router wiring
│   ├── config.py                # Pydantic Settings configuration & validation
│   ├── clients/
│   │   ├── __init__.py
│   │   ├── http_client.py       # Reusable lifespan-managed httpx.AsyncClient wrapper
│   │   └── service_registry.py  # Centralized service definitions & route precedence rules
│   ├── core/
│   │   ├── __init__.py
│   │   ├── exceptions.py        # Standard error classes, handlers, & error sanitization
│   │   └── logging.py           # Structured JSON logging with request_id contextvar
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── correlation.py       # ASGI X-Request-ID validation & propagation middleware
│   └── routes/
│       ├── __init__.py
│       ├── health.py            # GET /health self-check endpoint
│       └── proxy.py             # Explicit /api/v1 service proxy route handlers
├── tests/
│   ├── __init__.py
│   ├── test_health.py           # Startup, /health, lifecycle, CORS, & config validation tests
│   ├── test_correlation.py      # X-Request-ID generation, validation, forwarding, & logs
│   ├── test_proxy.py            # Routing precedence, HTTP forwarding, & header filtering
│   └── test_error_handling.py   # 4xx/5xx handling, timeouts, connect failures, & 404s
├── .env.example
├── Dockerfile
├── README.md
└── requirements.txt
```

## Configuration

Copy `.env.example` to `.env` before running locally (never commit `.env` or real secrets):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `APP_NAME` | `CodeBridge API Gateway` | FastAPI application title |
| `APP_ENV` | `development` | Runtime environment (`development`, `staging`, `production`, `test`) |
| `DEBUG` | `false` | FastAPI debug flag |
| `HOST` | `0.0.0.0` | Bind host for Uvicorn |
| `PORT` | `8000` | Bind port for Uvicorn |
| `API_V1_PREFIX` | `/api/v1` | Versioned API path prefix |
| `LOG_LEVEL` | `INFO` | Structured logger level |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Explicit comma-separated or JSON array origins |
| `CORS_ALLOW_CREDENTIALS` | `false` | Cannot be `true` when `*` is in `CORS_ORIGINS` |
| `REQUEST_TIMEOUT_SECONDS` | `10.0` | Downstream HTTP request timeout budget |
| `CONNECT_TIMEOUT_SECONDS` | `5.0` | Downstream TCP connection timeout (capped by request timeout) |
| `HTTP_MAX_CONNECTIONS` | `100` | HTTPX connection pool limit |
| `HTTP_MAX_KEEPALIVE_CONNECTIONS` | `20` | HTTPX keep-alive connection limit |
| `*_SERVICE_URL` | `http://localhost:8001`–`8012` | Internal base URL for each of the 12 microservices |

## Service Registry & Routing Conventions

All `/api/v1` proxy routes are registered explicitly from [`ServiceRegistry`](app/clients/service_registry.py) sorted by route specificity so project-scoped GitHub integration routes match before generic project routes:

| Public Prefix (`/api/v1/...`) | Target Microservice | Env Variable | Default Local URL |
| :--- | :--- | :--- | :--- |
| `/projects/{project_id}/github` *(priority)*, `/github` | `github-service` | `GITHUB_SERVICE_URL` | `http://localhost:8003` |
| `/auth`, `/freelancers`, `/profile` | `identity-service` | `IDENTITY_SERVICE_URL` | `http://localhost:8001` |
| `/projects` | `project-service` | `PROJECT_SERVICE_URL` | `http://localhost:8002` |
| `/code-context` | `code-context-service` | `CODE_CONTEXT_SERVICE_URL` | `http://localhost:8004` |
| `/workspace` | `workspace-service` | `WORKSPACE_SERVICE_URL` | `http://localhost:8005` |
| `/submissions`, `/review` | `review-service` | `REVIEW_SERVICE_URL` | `http://localhost:8006` |
| `/verification` | `verification-service` | `VERIFICATION_SERVICE_URL` | `http://localhost:8007` |
| `/payment` | `payment-service` | `PAYMENT_SERVICE_URL` | `http://localhost:8008` |
| `/notifications` | `notification-service` | `NOTIFICATION_SERVICE_URL` | `http://localhost:8009` |
| `/admin` | `admin-service` | `ADMIN_SERVICE_URL` | `http://localhost:8010` |
| `/audit` | `audit-service` | `AUDIT_SERVICE_URL` | `http://localhost:8011` |
| `/observability` | `observability-service` | `OBSERVABILITY_SERVICE_URL` | `http://localhost:8012` |

## Available Endpoints

- `GET /health` — Checks only the API Gateway process (`{"status": "healthy", "service": "api-gateway", "version": "1.0.0"}`). Works even when all 12 microservices are offline.
- `/api/v1/<route-group>[/<subpath>]` — Explicit forwarding routes (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`) mapped via `ServiceRegistry`.

## Error Response Format

All Gateway-generated and normalized error responses return:

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

Supported error codes include `VALIDATION_ERROR` (`400`/`422`), `NOT_FOUND` (`404`), `SERVICE_UNAVAILABLE` (`502`/`503`), `SERVICE_TIMEOUT` (`504`), and `INTERNAL_SERVER_ERROR` (`500`/`502`), plus meaningful preserved `4xx` downstream codes.

## Local Setup, Running & Testing

Run commands from inside `api-gateway/`:

```bash
# 1. Create and activate an isolated virtual environment
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

# 2. Install Gateway dependencies
pip install -r requirements.txt

# 3. Run the API Gateway locally
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. Run automated tests (downstream services are mocked; no DB/Docker required)
pytest -v
```
