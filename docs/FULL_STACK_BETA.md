# Full-stack beta application

This branch adds a web application around the existing Python DHQA engine. The CLI and Streamlit demo remain available; the application layer does not replace or fork the core engine.

## What is implemented

- FastAPI application with versioned workspace, connection, catalog, contract, upload, run, incident, overview and artifact endpoints.
- Supabase JWT verification using a configured JWKS endpoint. A development identity header is accepted only when APP_ENV=development and DEV_AUTH_ENABLED=true.
- Workspace membership checks on application data.
- PostgreSQL-compatible SQLAlchemy models and an initial Alembic migration.
- Fernet encryption for DataHub personal access tokens at rest.
- DataHub GraphQL metadata search, dataset retrieval and catalog sync.
- Optional, best-effort DataHub assertion and incident writeback using the existing async GraphQL client. It is disabled by default and can be enabled with DATAHUB_WRITEBACK_ENABLED=true after granting mutation permissions.
- CSV upload validation, configurable size and row limits, catalog-column matching and SHA-256 metadata.
- Redis/RQ background execution using the existing contract engine.
- Persistent quality results, incident evidence, generated SQL/schema/pytest/PR artifacts.
- Bounded multi-parent lineage investigation. Ancestors without uploaded data are explicitly marked unverified, not reported as clean.
- Next.js dashboard with magic-link sign-in when Supabase is configured, and a development-only identity for local Docker evaluation.
- Docker Compose for the application API, worker, frontend, PostgreSQL and Redis.

## Beta boundary

The first beta execution adapter is CSV-backed. DataHub supplies metadata and lineage; uploaded CSVs supply row-level data to the existing pandas checks. This is not yet a Snowflake, BigQuery or Databricks query adapter. A production deployment should use durable object storage for uploads, configure a managed database and Redis, and enable Supabase authentication.

The DataHub connection is an outbound server-side request. In non-development environments, configure DATAHUB_ALLOWED_HOSTS with the exact approved hostnames. This is a required SSRF boundary for hosted deployments.

## Local Docker setup

1. Install Docker Desktop (or Docker Engine with Compose).
2. Copy infra/.env.example to the repository root as .env.
3. Generate an encryption key:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

4. Set the generated value as APP_ENCRYPTION_KEY in .env.
5. For a local-only session, keep APP_ENV=development, DEV_AUTH_ENABLED=true, and NEXT_PUBLIC_DEV_USER=local-beta-user.
6. Start the stack:

   ```bash
   docker compose --env-file .env -f infra/docker-compose.app.yml up --build
   ```

7. Open http://localhost:3000. The API is available at http://localhost:8000; interactive API documentation is at http://localhost:8000/docs.

The initial application migration is run automatically by the API container before Uvicorn starts. Uploads are stored in the shared uploads Docker volume so both API and worker can access the same data.

### Connecting the local DataHub Quickstart

The API runs inside Docker. If DataHub is running on the host at port 8080, use http://host.docker.internal:8080 as the GMS URL. The Compose API/worker services map host.docker.internal to the host gateway. For DataHub in another container network or remote deployment, use a URL reachable from the API container.

The current DataHub quickstart in the repository root docker-compose.yml is a separate metadata stack. Start and validate it independently before adding the connection in Testagent.

### Hosted beta configuration

For a hosted environment:

- Set APP_ENV=production and DEV_AUTH_ENABLED=false.
- Set SUPABASE_JWKS_URL, for example https://PROJECT_REF.supabase.co/auth/v1/.well-known/jwks.json.
- Set SUPABASE_ISSUER to https://PROJECT_REF.supabase.co/auth/v1.
- Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY at frontend image build time.
- Set NEXT_PUBLIC_DEV_USER to an empty value for hosted builds. The local development identity must not be baked into a production frontend.
- Configure both SUPABASE_JWKS_URL and SUPABASE_ISSUER. The API fails closed in production if either is missing.
- Set DATAHUB_ALLOWED_HOSTS to an explicit comma-separated host allowlist.
- Enable DATAHUB_WRITEBACK_ENABLED=true only after confirming the DataHub token can create/update assertions and incidents.
- Set CORS_ORIGINS and NEXT_PUBLIC_API_URL to the deployed HTTPS origins.
- Use a strong unique APP_ENCRYPTION_KEY, managed database, managed Redis, and a persistent upload volume or object storage adapter.
- Restrict Supabase sign-up/invitation policy to the intended beta cohort.

Never use the development identity header in a hosted deployment. Do not commit .env files or access tokens.

## API outline

All authenticated application endpoints are under /api/v1.

- GET /workspaces, POST /workspaces
- GET /workspaces/{workspace_id}/connections, POST /workspaces/{workspace_id}/connections
- POST /workspaces/{workspace_id}/connections/{connection_id}/test
- POST /workspaces/{workspace_id}/connections/{connection_id}/sync
- GET /workspaces/{workspace_id}/datasets
- GET /workspaces/{workspace_id}/datasets/{dataset_id}
- PUT /workspaces/{workspace_id}/datasets/{dataset_id}/contract
- GET /workspaces/{workspace_id}/datasets/{dataset_id}/contract/versions
- POST /workspaces/{workspace_id}/datasets/{dataset_id}/uploads
- POST /workspaces/{workspace_id}/runs, GET /workspaces/{workspace_id}/runs
- GET /workspaces/{workspace_id}/runs/{run_id}
- GET /workspaces/{workspace_id}/runs/{run_id}/artifacts/{artifact_id}
- GET /workspaces/{workspace_id}/incidents
- PATCH /workspaces/{workspace_id}/incidents/{incident_id}
- GET /workspaces/{workspace_id}/overview
- GET /workspaces/{workspace_id}/audit

Liveness and readiness endpoints are /healthz and /readyz.

## Development without Docker

Install the application and development extras:

   ```bash
   python -m venv .venv
   # activate the virtual environment
   pip install -e ".[app,dev]"
   alembic upgrade head
   uvicorn dhqa_api.main:app --reload
   ```

Run a Redis server separately, then start a worker in another terminal:

   ```bash
   rq worker --url redis://localhost:6379/0 testagent
   ```

For frontend development:

   ```bash
   cd web
   npm install
   npm run dev
   ```

Set the required environment variables in the repository-root .env. For a local frontend/API connection set NEXT_PUBLIC_API_URL=http://localhost:8000 and NEXT_PUBLIC_DEV_USER=local-beta-user.

## Known limitations to close before public beta

- CSV files are stored on a shared local Docker volume. Hosted beta must use durable object storage or an explicitly persistent volume with backup and retention.
- Row-level warehouse adapters are not included; use CSV upload for execution.
- The dashboard is a first vertical slice, not a complete set of separately routed catalog, contract, lineage and incident workspaces.
- Contract history is persisted, but a dedicated history/restore UI is not yet included.
- RQ jobs have durable run records, but cancellation, user-configured retry policies and scheduled runs are not implemented.
- DataHub assertion/incident writeback is implemented as an opt-in worker step. Testagent always persists local evidence first; an external writeback failure is recorded in the run summary and does not discard the quality result. Validate mutation permissions against a real DataHub instance before enabling it.
- Supabase magic-link sign-in is implemented; production invitation policy and organization lifecycle need configuration.
- The initial Alembic revision creates the initial schema from SQLAlchemy metadata. Future schema changes should use explicit Alembic operations and migrations.
- A real DataHub deployment must be used to validate GraphQL schema compatibility, authentication and lineage response semantics before inviting external testers.

## Verification

CI installs the app and dev dependency extras, runs compile checks and the Python test suite, then installs the Next.js dependencies and runs TypeScript and production-build checks. Live DataHub connectivity still requires a separately provisioned instance and credentials; no live integration test is represented as passing by the offline suite.
