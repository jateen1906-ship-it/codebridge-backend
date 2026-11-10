# CodeBridge GitHub Service (`github-service`)

> **Status:** Phase 4 implemented — standalone asynchronous FastAPI microservice owning GitHub OAuth App integration, encrypted OAuth tokens, one-repository-per-project linking, and bounded read-only repository inspection.

- **Default Port:** `8003`
- **Database Ownership:** `codebridge_github` (PostgreSQL with async `asyncpg` & Alembic)
- **Public Entry Point (via API Gateway):**
  - `/api/v1/github/...`
  - `/api/v1/projects/{project_id}/github/...` *(priority route in Gateway `ServiceRegistry`)*

---

## Architecture Decision: GitHub OAuth App

GitHub Service uses a **GitHub OAuth App** (not a GitHub App), matching the architecture decision for CodeBridge:

- Authorization redirect target: fixed `https://github.com/login/oauth/authorize`
- Token exchange target: fixed `https://github.com/login/oauth/access_token`
- REST API target: fixed `https://api.github.com`
- Scopes configured via `GITHUB_OAUTH_SCOPES` (default: `repo read:user`)
- Phase 4 deliberately excludes repository cloning, workspace provisioning, commits, pushes, branches/PR creation, webhooks, and RAG indexing (owned by future Workspace and Code Context services).

---

## Security & Isolation Architecture

```text
Caller ──▶ API Gateway (:8000) ──▶ GitHub Service (:8003)
                                       │
                                       ├── AuthN: GET /api/v1/auth/me (Bearer JWT + X-Request-ID)
                                       │      ──▶ Identity Service (:8001)
                                       │
                                       ├── Ownership / Workflow Check: GET /api/v1/projects/{project_id}
                                       │      (Bearer JWT + X-Request-ID) ──▶ Project Service (:8002)
                                       │
                                       ├── GitHub OAuth / REST (Fixed HTTPS hosts only, follow_redirects=False)
                                       │      ──▶ github.com & api.github.com
                                       │
                                       └── Persistence: codebridge_github (PostgreSQL via asyncpg)
```

### Core Security Guarantees
1. **Token Encryption at Rest (AES-256-GCM):**
   - Plaintext GitHub OAuth tokens are never stored in the database, logged, or returned in any HTTP response.
   - `TokenEncryptionService` encrypts tokens with AES-256-GCM (`v1:<base64(nonce + ciphertext)>`) using a unique 96-bit random nonce per write and AAD bound to `codebridge:github-oauth-token:{owner_id}`.
   - Disconnection overwrites `encrypted_access_token` with `"[REVOKED]"`.
2. **OAuth State Security:**
   - Every `/oauth/authorize` request creates a cryptographically random URL-safe state stored strictly as a SHA-256 digest (`state_hash`) bound to `(owner_id, project_id)` with `expires_at`.
   - `/oauth/callback` rejects caller-supplied `owner_id` / `project_id` query overrides, verifies optional authenticated owner match, and atomically consumes unused/unexpired state (`UPDATE ... WHERE consumed_at IS NULL AND expires_at > :now`) **before** contacting GitHub.
3. **Anti-SSRF & Read-Only Enforcement:**
   - `GitHubHTTPClient` targets fixed GitHub hosts (`https://github.com/login/oauth/access_token` and `https://api.github.com`) with `follow_redirects=False` and a 1 MiB transport safety ceiling. It never fetches arbitrary user-supplied URLs or external `download_url` targets.
4. **Path Traversal & Git Ref Hardening:**
   - All `path`, `sha`, and `ref` inputs are normalized and checked against traversal (`..`, `.`, `\`, `%2e`, `%2f`, `%5c`, null bytes, control chars, URL schemes) and ref injection before making upstream GitHub requests.
5. **Strict Bounds:**
   - Pagination (`GITHUB_API_PAGE_SIZE`, `GITHUB_API_MAX_PAGES`) and repository content reads (`GITHUB_CONTENTS_MAX_BYTES`, `GITHUB_CONTENTS_MAX_ENTRIES`) are capped, and binary files are safely flagged (`is_binary: true`, `content: null`).
6. **Fail-Closed Verification & Zero Secret Leakage:**
   - Identity Service and Project Service outages fail closed with `503`.
   - Structured JSON logging allowlists fields, scrubs secret patterns (`Bearer ...`, `gh[opu]_...`, `client_secret=...`), and forwards `X-Request-ID`.

---

## Database Schema (`codebridge_github`)

| Table | Purpose | Key Constraints & Indexes |
| :--- | :--- | :--- |
| `github_connections` | Stores an owner's GitHub identity & AES-256-GCM encrypted OAuth access token | PK `id`, indexed `owner_id`, `github_user_id`, `status` (`ACTIVE`/`DISCONNECTED`/`REVOKED`), partial unique index `uq_github_connections_active_owner` on `(owner_id) WHERE status = 'ACTIVE'` |
| `oauth_states` | Short-lived, single-use hashed CSRF states binding OAuth flows to `(owner_id, project_id)` | PK `id`, unique `state_hash`, indexed `owner_id`, `project_id`, `expires_at`, nullable `consumed_at` |
| `project_repositories` | Links a CodeBridge project to one active GitHub repository & stores authoritative metadata | PK `id`, FK `github_connection_id -> github_connections.id (ON DELETE RESTRICT)`, partial unique index `uq_project_repositories_one_active_per_project` on `(project_id) WHERE connection_status = 'ACTIVE'`, composite index `(project_id, connection_status)` |

---

## Environment Variables

Copy `.env.example` to `.env`:

| Variable | Default / Example | Purpose |
| :--- | :--- | :--- |
| `PORT` | `8003` | HTTP server port |
| `APP_ENV` | `development` | `development`, `test`, `staging`, `production` (staging/prod reject SQLite, `DEBUG=true`, placeholder secrets, default keys) |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:change_me_local_only@localhost:5432/codebridge_github` | Async PostgreSQL (`postgresql+asyncpg://`) or isolated SQLite test URL |
| `IDENTITY_SERVICE_URL` | `http://localhost:8001` | Peer Identity Service base URL |
| `PROJECT_SERVICE_URL` | `http://localhost:8002` | Peer Project Service base URL |
| `GITHUB_CLIENT_ID` | *(OAuth App client ID)* | Public GitHub OAuth App Client ID |
| `GITHUB_CLIENT_SECRET` | *(OAuth App client secret)* | Server-side OAuth App Client Secret (never logged or returned) |
| `GITHUB_OAUTH_CALLBACK_URL` | `http://localhost:8000/api/v1/github/oauth/callback` | Registered OAuth App callback URL via API Gateway |
| `GITHUB_OAUTH_SCOPES` | `repo read:user` | OAuth scopes requested during authorization |
| `GITHUB_TOKEN_ENCRYPTION_KEY` | *(base64-encoded 32 bytes)* | AES-256-GCM master encryption key for tokens at rest |
| `FRONTEND_OAUTH_SUCCESS_URL` | `""` (optional) | Optional non-secret redirect target after OAuth callback |
| `FRONTEND_OAUTH_FAILURE_URL` | `""` (optional) | Reserved optional frontend error redirect URL |
| `GITHUB_API_PAGE_SIZE` | `50` | Maximum items per GitHub API page |
| `GITHUB_API_MAX_PAGES` | `5` | Maximum paginated page index |
| `GITHUB_CONTENTS_MAX_BYTES` | `262144` | Maximum decoded bytes returned per file read |
| `GITHUB_CONTENTS_MAX_ENTRIES` | `200` | Maximum directory entries returned per contents listing |

Generate a production encryption key:

```bash
python -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

---

## Endpoints

| Method | Path | Auth / Role Required | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Public | Lightweight service liveness probe |
| `GET` | `/ready` | Public | Safe database connectivity check (`SELECT 1`) |
| `GET` | `/api/v1/github/oauth/authorize` | `OWNER` (must own `project_id`) | Redirect (`302`) to GitHub OAuth with single-use state |
| `GET` | `/api/v1/github/oauth/callback` | State-bound (optional `OWNER` check if header supplied) | Exchange code, encrypt & persist token, return safe metadata or redirect |
| `DELETE` | `/api/v1/github/connection` | `OWNER` | Disconnect owner GitHub OAuth & unlink inactive projects (`409` on active workflows) |
| `GET` | `/api/v1/github/repositories` | `OWNER` | Paginated listing of accessible GitHub repositories |
| `POST` | `/api/v1/projects/{project_id}/github/connect` | `OWNER` (must own project) | Verify & bind one active repository to project (`201`, `409` if already connected) |
| `GET` | `/api/v1/projects/{project_id}/github/status` | Scoped via Project Service (`OWNER` own, `FREELANCER` `OPEN`/assigned, `ADMIN` all) | Safe repository connection status without tokens |
| `DELETE` | `/api/v1/projects/{project_id}/github/disconnect` | `OWNER` (must own project) | Unlink repository from one project (`409` on active freelancer workflow) |
| `GET` | `/api/v1/projects/{project_id}/github/branches` | `OWNER` (must own project) | Read-only branch list |
| `GET` | `/api/v1/projects/{project_id}/github/contents` | `OWNER` (must own project) | Bounded read-only file/directory contents |
| `GET` | `/api/v1/projects/{project_id}/github/commits` | `OWNER` (must own project) | Paginated read-only commit history |

---

## Local Setup, Migrations, and Testing

```bash
cd services/github-service
python -m venv .venv
# Activate .venv (Windows PowerShell: .\.venv\Scripts\Activate.ps1)
pip install -r requirements.txt

# Apply Alembic migrations against PostgreSQL DATABASE_URL
alembic upgrade head

# Run standalone service on port 8003
uvicorn app.main:app --host 0.0.0.0 --port 8003
```

Run tests (isolated SQLite via `aiosqlite` + deterministic `httpx.MockTransport` routers):

```bash
pytest -v
```

Verify static PostgreSQL DDL generation offline:

```bash
alembic upgrade head --sql
```
