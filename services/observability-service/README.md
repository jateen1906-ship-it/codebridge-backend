# CodeBridge Observability Service (`8012`)

Operational monitoring, health/readiness probing, and telemetry aggregation across CodeBridge.

## Capabilities
- Concurrent `/health` and `/ready` probing across `api-gateway` (`8000`) and all 12 microservices (`8001`–`8012`) via `GET /api/v1/observability/services` (`ADMIN`).
- Authenticated internal telemetry intake (`POST /api/v1/observability/telemetry`) via constant-time `X-Service-Key` or `ADMIN` Bearer token with secret scrubbing.
- Aggregated service latency (`avg`, `p95`, `max`) and error rate metrics (`GET /api/v1/observability/metrics`, `ADMIN`).
- Recent sanitized error stream (`GET /api/v1/observability/errors`, `ADMIN`).
