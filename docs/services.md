# CodeBridge Microservices Catalog

> **Phase 0 Note:** Services listed below are initialized as isolated directory structures only. Business logic, endpoints, and persistence are deferred to service-specific implementation phases.

## Service Registry & Planned Ports

| # | Service | Planned Local Port | Directory |
| :-: | :--- | :---: | :--- |
| 0 | `api-gateway` (Edge Gateway) | `8000` | `api-gateway/` |
| 1 | `identity-service` | `8001` | `services/identity-service/` |
| 2 | `project-service` | `8002` | `services/project-service/` |
| 3 | `github-service` | `8003` | `services/github-service/` |
| 4 | `code-context-service` | `8004` | `services/code-context-service/` |
| 5 | `workspace-service` | `8005` | `services/workspace-service/` |
| 6 | `review-service` | `8006` | `services/review-service/` |
| 7 | `verification-service` | `8007` | `services/verification-service/` |
| 8 | `payment-service` | `8008` | `services/payment-service/` |
| 9 | `notification-service` | `8009` | `services/notification-service/` |
| 10 | `admin-service` | `8010` | `services/admin-service/` |
| 11 | `audit-service` | `8011` | `services/audit-service/` |
| 12 | `observability-service` | `8012` | `services/observability-service/` |

---

## Microservice Responsibilities

### 1. `identity-service` (`8001`)
Owns user authentication, identity lifecycle, and access roles across the platform.
- User identity management
- User profiles
- Freelancer profiles
- Authentication
- Role-based access control (RBAC)
- Owner, freelancer, and admin identities

### 2. `project-service` (`8002`)
Owns the core project and task lifecycle for software completion workflows.
- Project creation and management
- Project requirements
- Tasks and task breakdown
- Freelancer assignments
- Deadlines
- Project lifecycle state transitions

### 3. `github-service` (`8003`) — Implemented (Phase 4)
Owns external source-control integration with GitHub via **GitHub OAuth App** and read-only GitHub REST APIs (`https://api.github.com`):
- Single-use CSRF OAuth state initiation & callback exchange (`GET /api/v1/github/oauth/authorize`, `GET /api/v1/github/oauth/callback`)
- AES-256-GCM encrypted OAuth credential storage bound to `owner_id` (plus account disconnect `DELETE /api/v1/github/connection`)
- Owner repository discovery (`GET /api/v1/github/repositories`)
- One-repository-per-project linking & lifecycle management (`POST /api/v1/projects/{project_id}/github/connect`, `DELETE .../disconnect`, `GET .../status`)
- Bounded read-only repository access (`GET .../branches`, `GET .../contents`, `GET .../commits`) without cloning or workspace mutations

### 4. `code-context-service` (`8004`)
Owns repository intelligence, retrieval-augmented code understanding, and repository health insights.
- Repository analysis
- Code understanding
- Retrieval-Augmented Generation (RAG)
- AI code insights
- Documentation retrieval
- Repository health reports

### 5. `workspace-service` (`8005`)
Owns isolated developer execution environments for working on assigned tasks.
- Isolated coding workspaces
- Workspace creation
- Workspace lifecycle management
- Start and stop operations
- Docker integration (planned in a future phase)

### 6. `review-service` (`8006`)
Owns task submission intake and owner review/approval workflows.
- Task submissions
- Submission records
- Owner review workflows
- Review feedback
- Revision requests
- Approval workflow

### 7. `verification-service` (`8007`)
Owns automated verification of submitted work prior to or alongside owner approval.
- Automated tests execution coordination
- Acceptance testing
- Code validation
- Test results tracking
- Verification reports

### 8. `payment-service` (`8008`)
Owns simulated financial escrow workflows and payment tracking.
- Simulated escrow accounts/holds
- Payment authorization
- Payment hold
- Payment release
- Refund simulation
- Payment audit records

### 9. `notification-service` (`8009`)
Owns user-facing alerts, announcements, and notification history.
- User notifications
- System announcements
- Notification history
- Future WebSocket delivery support

### 10. `admin-service` (`8010`)
Owns platform moderation and administrative controls.
- User moderation
- Project moderation
- Moderation reports
- Administrative operations

### 11. `audit-service` (`8011`)
Owns immutable platform activity records and compliance/audit trails.
- Activity logs
- User activity records
- Project activity records
- Workspace access records
- Audit history querying

### 12. `observability-service` (`8012`)
Owns platform-wide operational visibility across microservices.
- Health monitoring
- Readiness monitoring
- Service metrics aggregation
- Operational monitoring

---

## Internal Service Anatomy

Every business microservice follows a consistent internal layered package layout:

- `app/api/routes/` — FastAPI HTTP route handlers
- `app/core/` — Service settings, startup hooks, and internal config
- `app/models/` — SQLAlchemy ORM models (owned exclusively by that service)
- `app/schemas/` — Pydantic request/response schemas
- `app/repositories/` — Data access layer interacting with the service's own DB
- `app/services/` — Domain business logic
- `app/clients/` — HTTPX clients for calling peer microservices synchronously
- `tests/` — Service-scoped Pytest unit and API tests

