# CodeBridge Microservices Catalog

All **13 services** (`api-gateway` + 12 domain microservices) are implemented as independent FastAPI services on ports `8000`–`8012`.

## Service Registry & Ports

| # | Service | Port | Owned Database | Directory |
| :-: | :--- | :---: | :--- | :--- |
| 0 | `api-gateway` (Edge Gateway) | `8000` | Stateless | `api-gateway/` |
| 1 | `identity-service` | `8001` | `codebridge_identity` | `services/identity-service/` |
| 2 | `project-service` | `8002` | `codebridge_project` | `services/project-service/` |
| 3 | `github-service` | `8003` | `codebridge_github` | `services/github-service/` |
| 4 | `code-context-service` | `8004` | `codebridge_code_context` | `services/code-context-service/` |
| 5 | `workspace-service` | `8005` | `codebridge_workspace` | `services/workspace-service/` |
| 6 | `review-service` | `8006` | `codebridge_review` | `services/review-service/` |
| 7 | `verification-service` | `8007` | `codebridge_verification` | `services/verification-service/` |
| 8 | `payment-service` | `8008` | `codebridge_payment` | `services/payment-service/` |
| 9 | `notification-service` | `8009` | `codebridge_notification` | `services/notification-service/` |
| 10 | `admin-service` | `8010` | `codebridge_admin` | `services/admin-service/` |
| 11 | `audit-service` | `8011` | `codebridge_audit` | `services/audit-service/` |
| 12 | `observability-service` | `8012` | `codebridge_observability` | `services/observability-service/` |

---

## Microservice Responsibilities

1. **`identity-service` (`8001`):** Argon2 user authentication, HS256 JWT access/refresh token rotation with family revocation on reuse, user & freelancer profiles, strict RBAC (`OWNER`, `FREELANCER`, `ADMIN`), and restricted `/auth/admin/users` management.
2. **`project-service` (`8002`):** Project creation/publishing/updates/cancellation, structured `ProjectRequirement`s, transactional single-freelancer assignment (`select-freelancer`), lifecycle state transitions (`OPEN -> ASSIGNED -> IN_PROGRESS / CHANGES_REQUESTED -> COMPLETED`), and review-driven workflow synchronization.
3. **`github-service` (`8003`):** GitHub OAuth App single-use CSRF state flow, AES-256-GCM encrypted token storage, owner repository listing, one-repo-per-project linking, bounded read-only tree/file/commit queries, and validated zipball repository snapshots (`/snapshot`).
4. **`code-context-service` (`8004`):** Asynchronous/bounded repository ingestion exclusively via `github-service`, secret filtering, LangChain language-aware chunking into per-project ChromaDB collections, grounded semantic RAG query (`POST /query`), and architectural health insights.
5. **`workspace-service` (`8005`):** Isolated developer execution workspaces with zip-slip/symlink/decompression-bomb protection, path containment, Docker (`--network none`, CPU/RAM/PIDs caps) + mock runtime adapter, container start/stop/refresh/logs/destroy lifecycle, and TTL cleanup.
6. **`review-service` (`8006`):** Versioned freelancer work submissions (`DRAFT -> SUBMITTED -> IN_REVIEW -> CHANGES_REQUESTED / APPROVED / REJECTED`), file change manifests, inline comments, owner review decisions, authoritative `project-service` status sync, and notification/audit emission.
7. **`verification-service` (`8007`):** Tamper-evident `VerifiedWorkRecord` issuance strictly gated on `review-service` `APPROVED` status, canonical JSON SHA-256 hashing + HMAC-SHA256 signing, opaque `cbv_*` unauthenticated public proof lookup (zero PII), signature verification, and formal `REVOKED`/`CORRECTED` lifecycle.
8. **`payment-service` (`8008`):** Simulated escrow accounts (`PENDING -> FUNDED -> HELD -> RELEASED / REFUNDED / CANCELLED`), `project-service` budget/currency alignment, SHA-256 payload-bound `Idempotency-Key` protection, hard release block unless `review-service` confirms `APPROVED`, immutable ledger transactions, and refund workflows.
9. **`notification-service` (`8009`):** Authenticated internal/ADMIN notification event ingestion (`X-Service-Key` constant-time check) with `(user_id, idempotency_key)` deduplication, user preferences, mock email delivery logs, and user inbox read/read-all endpoints.
10. **`admin-service` (`8010`):** Strict `ADMIN`-only moderation, cross-service user suspension/restoration via `identity-service`, platform project/audit inspection, abuse report creation & resolution, and immutable admin action log.
11. **`audit-service` (`8011`):** Append-only `AuditLog` ingestion (zero `PUT`/`PATCH`/`DELETE` routes), recursive secret/token/DB-URL scrubbing, `ADMIN`-global / user-scoped query filtering, and project-member-scoped audit trail retrieval.
12. **`observability-service` (`8012`):** Concurrent `/health` & `/ready` probes across `api-gateway` and all 12 microservices, secret-scrubbed telemetry ingestion (`X-Service-Key` or `ADMIN`), aggregated per-service request/error/p95 latency metrics, and sanitized error log streams (`ADMIN`).
