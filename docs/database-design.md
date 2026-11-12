# Database Design & Ownership

> **Phase 0 Note:** No PostgreSQL instances, connection strings, SQLAlchemy models, or Alembic migration scripts are created in Phase 0. This document establishes data ownership boundaries before any schema design begins.

## 1. Database-Per-Service Principle

CodeBridge enforces the **database-per-service** pattern across all business microservices:

- **Dedicated Logical Database per Service:** Each microservice that persists state owns its own isolated PostgreSQL database (and its own SQLAlchemy models and Alembic migration history).
- **Zero Shared Database Access:** No service may connect to, query, join against, or mutate another service's database tables.
- **No Cross-Database Foreign Keys:** References to entities owned by another service (such as `user_id`, `project_id`, `repo_id`, or `submission_id`) are stored as opaque identifiers (e.g., UUIDs) and validated or enriched via synchronous HTTP/REST APIs when required.
- **Independent Schema Evolution:** Each service evolves and migrates its schema independently using Alembic without coordinating shared migrations.

## 2. Planned Database Ownership Mapping

| Component | Database Owned (Planned) | Primary Stored Domain Concepts (Future) |
| :--- | :--- | :--- |
| `api-gateway` | **None** (Stateless) | Does not persist domain data |
| `identity-service` | `codebridge_identity` | Users, credentials, profiles, freelancer profiles, roles/permissions |
| `project-service` | `codebridge_project` | Projects, requirements, tasks, freelancer assignments, deadlines |
| `github-service` | `codebridge_github` | GitHub OAuth tokens/connections, linked repositories, repo & commit metadata |
| `code-context-service` | `codebridge_code_context` | Repository analysis snapshots, embeddings/RAG indexes, health reports |
| `workspace-service` | `codebridge_workspace` | Workspace definitions, container/session metadata, lifecycle state |
| `review-service` | `codebridge_review` | Task submissions, review decisions, feedback comments, revision requests |
| `verification-service` | `codebridge_verification` | Verification runs, test suites, validation statuses, verification reports |
| `payment-service` | `codebridge_payment` | Simulated escrow holds, authorizations, releases, refunds, payment ledger |
| `notification-service` | `codebridge_notification` | User notifications, system announcements, delivery/read history |
| `admin-service` | `codebridge_admin` | Moderation cases, abuse/incident reports, administrative action records |
| `audit-service` | `codebridge_audit` | Immutable activity logs, user/project activity, workspace access records |
| `observability-service` | Optional / Telemetry Store | Aggregated health snapshots, readiness status, operational metrics metadata |

## 3. Planned Persistence Tooling

When individual services enter their implementation phases:
- **Database Engine:** PostgreSQL
- **ORM:** SQLAlchemy
- **Schema Migrations:** Alembic (initialized independently inside each stateful service)
- **Caching (Future):** `infrastructure/redis/` is reserved for future phase caching/session/ephemeral needs where explicitly justified, without replacing PostgreSQL as the system of record.

## 4. Cross-Service Data Consistency Strategy

Because distributed transactions (2PC) and message-broker sagas are intentionally excluded:
- Services record local state transitions atomically in their own database.
- Cross-service workflows (for example, triggering an audit log entry on `audit-service` or notifying a user via `notification-service`) are invoked synchronously over HTTP/REST with clear failure handling defined per workflow.
- Critical state ownership always remains single-writer: only `payment-service` writes payment/escrow records, only `project-service` writes task state, only `identity-service` writes identity/role records, etc.

## 5. Implemented Phase 4 Schema Summary — `github-service` (`codebridge_github`)

| Table | Purpose | Key Constraints |
| :--- | :--- | :--- |
| `github_connections` | Owner GitHub user ID, login, scopes, lifecycle status, and AES-256-GCM `encrypted_access_token` | Partial unique index `uq_github_connections_active_owner` on `(owner_id) WHERE status = 'ACTIVE'`; status `ACTIVE`, `DISCONNECTED`, `REVOKED` |
| `oauth_states` | Short-lived, single-use SHA-256 `state_hash` bound to `(owner_id, project_id)` | Unique `state_hash`, atomic `consumed_at` conditional update prior to OAuth code exchange |
| `project_repositories` | Project-to-repository association and authoritative GitHub repository metadata | FK `github_connection_id -> github_connections.id (ON DELETE RESTRICT)`, partial unique index `uq_project_repositories_one_active_per_project` on `(project_id) WHERE connection_status = 'ACTIVE'` |

## 6. Implemented Phase 5 Schema Summary — `code-context-service` (`codebridge_code_context`)

| Table | Purpose | Key Constraints |
| :--- | :--- | :--- |
| `repository_indexes` | Versioned project index state, branch, commit SHA, Chroma collection name, counts, and `is_active` flag | Unique `(project_id, index_version)`, unique `collection_name`, partial unique `uq_repo_index_one_active_per_project` on `(project_id) WHERE is_active = true` |
| `indexing_jobs` | Execution tracking & concurrency lock for `INITIAL` and `REINDEX` runs | FK `repository_index_id -> repository_indexes.id (CASCADE)`, partial unique `uq_indexing_job_one_inflight_per_project` on `(project_id) WHERE status IN ('PENDING', 'RUNNING')` |
| `indexed_files` | File inventory with language, SHA, size, status (`INDEXED`/`SKIPPED`/`FAILED`), and `skip_reason` | FK `repository_index_id -> repository_indexes.id (CASCADE)`, unique `(repository_index_id, file_path)` |
| `code_chunks` | Compact line-range metadata (`start_line`, `end_line`, `content_hash`, `vector_id`) mapped to ChromaDB | FK `repository_index_id` & `indexed_file_id (CASCADE)`, unique `(repository_index_id, chunk_identifier)` (raw chunk text lives in ChromaDB) |
| `repository_insights` | Persisted deterministic health diagnostics & optional labeled AI architectural summary (`JSON`/`JSONB`) | FK `repository_index_id -> repository_indexes.id (ON DELETE RESTRICT)` preserving historical insight traceability |


