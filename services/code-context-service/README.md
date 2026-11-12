# Code Context Service (`code-context-service`)

- **Local Port:** `8004`
- **Database Ownership:** `codebridge_code_context` (PostgreSQL async via `asyncpg` + SQLAlchemy 2.x + Alembic)
- **Vector Store:** ChromaDB (`CHROMA_MODE=persistent|ephemeral`) with isolated per-project versioned collections
- **Code Parsing & Embeddings:** LangChain (`langchain-text-splitters`, `langchain-core`) + configurable OpenAI / Ollama / mock adapters

## Implemented Capabilities

1. **Identity & Project Authorization**
   - Authenticates every caller token synchronously via `identity-service` (`GET /api/v1/auth/me`).
   - Verifies project ownership and active freelancer assignment strictly via `project-service` (`GET /api/v1/projects/{project_id}`).
   - Prevents unassigned freelancers on `OPEN` projects from reading or querying indexed private repository context.
2. **Read-Only GitHub Repository Ingestion via `github-service`**
   - Retrieves repository status, branch references, and directory/file contents **exclusively** via `github-service` (`/api/v1/projects/{project_id}/github/status`, `/branches`, `/contents`).
   - Never connects directly to `https://api.github.com` and never accesses or persists GitHub OAuth tokens.
   - Enforces directory exclusion (`.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`), sensitive-file blocking (`.env`, `.pem`, `.key`, `id_rsa`), content secret scanning, binary/empty checks, and bounded traversal/size limits.
3. **Versioned Language-Aware Chunking & ChromaDB Indexing**
   - Splits files with `langchain-text-splitters` (`RecursiveCharacterTextSplitter.from_language`) and tracks exact 1-based `start_line` / `end_line` spans.
   - Indexes dense vectors into versioned ChromaDB collections (`<prefix>_<project_hex>_v<version>`) before atomically switching `is_active`, preserving any active previous index if a reindex fails, and cleaning up retired collections beyond `INDEX_RETENTION_VERSIONS`.
4. **Grounded RAG Query & Repository Insights**
   - `POST /api/v1/code-context/projects/{project_id}/query` retrieves project-isolated chunks and synthesizes answers strictly from retrieved `<repository_evidence>` with citation validation, prompt-injection defense, and retrieval-only fallback when LLM is disabled or unavailable.
   - `POST /api/v1/code-context/projects/{project_id}/insights/generate` computes deterministic repository health checks (languages, manifest/CI/Dockerfile/lint detection, static test presence with explicit non-execution notice) plus an optional labeled AI summary.

## Endpoints

- `GET /health` & `GET /ready`
- `POST /api/v1/code-context/projects/{project_id}/index` (`OWNER` only)
- `POST /api/v1/code-context/projects/{project_id}/reindex` (`OWNER` only)
- `GET /api/v1/code-context/projects/{project_id}/index/status` (`OWNER`, actively assigned `FREELANCER`, `ADMIN`)
- `POST /api/v1/code-context/projects/{project_id}/query` (`OWNER`, actively assigned `FREELANCER`)
- `POST /api/v1/code-context/projects/{project_id}/insights/generate` (`OWNER` only)
- `GET /api/v1/code-context/projects/{project_id}/insights` (`OWNER`, actively assigned `FREELANCER`, `ADMIN`)

## Local Setup & Commands

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8004
.\.venv\Scripts\python.exe -m pytest -v
```
