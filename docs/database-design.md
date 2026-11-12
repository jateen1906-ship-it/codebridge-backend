# Database Design & Ownership

## 1. Database-Per-Service Isolation

CodeBridge strictly enforces the **database-per-service** pattern across all 12 domain microservices:
- Dedicated logical PostgreSQL database per service (`infrastructure/postgres/init-databases.sql`).
- Zero shared SQLAlchemy engines, sessions, ORM packages, or cross-database foreign keys.
- Independent per-service `alembic.ini` + `migrations/` schema histories.

## 2. Database Ownership Mapping

| Component | Owned Database | Key Tables / Domain Models |
| :--- | :--- | :--- |
| `api-gateway` | **None** *(Stateless)* | Edge proxy only |
| `identity-service` | `codebridge_identity` | `users`, `freelancer_profiles`, `refresh_tokens` |
| `project-service` | `codebridge_project` | `projects`, `project_requirements`, `assignments` |
| `github-service` | `codebridge_github` | `github_connections`, `oauth_states`, `project_repositories` |
| `code-context-service` | `codebridge_code_context` | `repository_indexes`, `indexing_jobs`, `indexed_files`, `code_chunks`, `repository_insights` (+ per-project ChromaDB collections) |
| `workspace-service` | `codebridge_workspace` | `workspaces`, `workspace_events` |
| `review-service` | `codebridge_review` | `submissions`, `submission_files`, `reviews`, `review_comments`, `review_events` |
| `verification-service` | `codebridge_verification` | `verified_work_records`, `verification_events` |
| `payment-service` | `codebridge_payment` | `escrow_payments`, `escrow_transactions`, `payment_events`, `payment_idempotency_records` |
| `notification-service` | `codebridge_notification` | `notifications`, `notification_preferences`, `email_delivery_logs` |
| `admin-service` | `codebridge_admin` | `moderation_reports`, `admin_actions` |
| `audit-service` | `codebridge_audit` | `audit_logs` (append-only, secret-scrubbed) |
| `observability-service` | `codebridge_observability` | `observability_telemetry_records` |

## 3. PostgreSQL Initialization & Alembic Migrations

- `infrastructure/postgres/init-databases.sql` provisions all 12 isolated databases automatically inside Docker Compose (`postgres` service).
- Each microservice container executes `alembic upgrade head` against its owned database prior to serving Uvicorn traffic.
- All migration scripts target PostgreSQL DDL (`upgrade head --sql` verified) and support SQLite (`sqlite+aiosqlite`) roundtrips for fast isolated automated tests.
