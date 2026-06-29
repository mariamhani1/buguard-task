# Buguard DarkAtlas — Asset Management (Track B: AI Applications)

A self-contained slice of the DarkAtlas Attack Surface Monitoring platform: a minimal
**FastAPI + PostgreSQL** asset inventory with a **LangChain** analysis layer that is
grounded in the stored data. Built for Track B (AI Applications).

## What it does

A single agentic `/analyze` endpoint (a LangChain tool-calling agent) exposes the four
mandatory capabilities, each backed by a grounded tool over the database:

| Capability | Tool | How it stays grounded |
|---|---|---|
| 1. Natural-language asset query | `search_assets` | Translates English into structured SQL filters: type, status, **tags**, **value substring**, and **certificate expiry** (expired / expiring-within-N-days). Returns only real rows. |
| 2. Risk scoring & summarization | `score_asset_risk` | Deterministic signals (expired/expiring certs, sensitive ports, EOL tech) are computed in **Python** (`lifecycle.py`); the LLM only summarizes them into a structured `RiskAssessment`. |
| 3. Enrichment & categorization | `enrich_asset` | Looks the asset up in the DB, classifies environment/category/criticality as a structured `Enrichment`, and **persists** it back into the asset's metadata. |
| 4. NL report generation | `generate_inventory_report` | Fed a deterministically-computed risk context; instructed to narrate only what is present. |
| (support) relationship graph | `get_asset_graph` | Fetches an asset together with its related assets. |

**Grounding is enforced three ways:** tools only ever return real DB rows; risk facts
are computed in code (not guessed by the model); a strict system prompt forbids
inventing assets, and tools return explicit "not found" instead of fabricating.

## Architecture

```
main.py        FastAPI app: import, list, graph, analyze endpoints; lifespan; error mapping
auth.py        API-key auth (org resolved from key) + per-org rate limiting
config.py      env-driven settings (no hard-coded secrets)
models.py      SQLAlchemy models: Asset (typed enums, JSONB, ARRAY), AssetRelationship
schemas.py     Pydantic: AssetImport (per-row validation) + structured tool outputs
crud.py        idempotent bulk upsert; pure merge_metadata / merge_tags helpers
services.py    grounded, org-scoped data access (query/get/enrich/graph/report context)
lifecycle.py   deterministic date + risk-signal computation
agent.py       LangChain tools + tool-calling agent
seed/          sample dataset (two tenants); seed.py loads it through the API
tests/         pure unit tests + Postgres-gated integration tests
```

## Setup & run

1. Create your env file and add your OpenAI key:

   ```bash
   cp .env.example .env
   # edit .env: set OPENAI_API_KEY and a strong POSTGRES_PASSWORD
   ```

2. Start the API + PostgreSQL:

   ```bash
   docker compose up --build -d
   ```

   API: `http://localhost:8000` · Swagger UI: `http://localhost:8000/docs`

3. Seed the sample dataset (two tenants) through the import endpoint:

   ```bash
   pip install httpx          # only if running seed.py on the host
   python seed.py             # or: docker compose exec web python seed.py
   ```

### Environment variables

| Var | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Async Postgres URL | derived from `POSTGRES_*` in compose |
| `POSTGRES_USER/PASSWORD/DB` | DB provisioning | `postgres` / `postgres` / `darkatlas_asm` |
| `OPENAI_API_KEY` | LLM provider key (read from env, never committed) | — |
| `LLM_MODEL` | Chat model | `gpt-4o-mini` |
| `API_KEYS` | `key:org` pairs; org is resolved from the key | DEV keys (see below) |
| `ANALYZE_RATE_LIMIT_PER_MIN` | per-org `/analyze` budget | `30` |
| `EXPIRING_SOON_DAYS` | "expiring soon" window | `30` |
| `MAX_IMPORT_BATCH` | max records per import | `5000` |

## Authentication & multi-tenancy

Every endpoint requires an `X-API-Key` header. The **organization is resolved from the
key server-side** — clients never assert their own tenant, so one org's data cannot leak
into another's view. Default DEV keys (override via `API_KEYS` in production):

- `dev-key-acme` → `org_acme`
- `dev-key-globex` → `org_globex`

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/assets/import` | Bulk import; per-row validation; idempotent. `200` ok / `207` partial / `422` all-failed |
| `GET` | `/api/v1/assets` | Filter by `type`, `status`, `tag` (repeatable), `value_contains`; `limit`/`offset` pagination |
| `GET` | `/api/v1/assets/{value}/graph` | Asset + its related assets |
| `POST` | `/api/v1/analyze` | Grounded LangChain agent (NL query / risk / enrich / report); rate-limited |
| `GET` | `/health` | Liveness |

```bash
# Import
curl -X POST localhost:8000/api/v1/assets/import \
  -H 'X-API-Key: dev-key-acme' -H 'Content-Type: application/json' \
  -d @seed/assets.json

# List production subdomains
curl 'localhost:8000/api/v1/assets?type=subdomain&tag=production' -H 'X-API-Key: dev-key-acme'

# Analyze
curl -X POST localhost:8000/api/v1/analyze \
  -H 'X-API-Key: dev-key-acme' -H 'Content-Type: application/json' \
  -d '{"prompt":"Show me all expired certificates on production subdomains."}'
```

## Example prompts & outputs

> Outputs are LLM-generated and will vary in wording; the **facts** are grounded in the
> seed dataset (`seed/assets.json`). Assumes a run date in mid-2026 (so `cert1`
> @ 2025-01-02 is expired and `cert2` @ 2026-07-15 is expiring soon).

**Prompt:** `"Show me all expired certificates."`
**Behind the scenes:** `search_assets(asset_type="certificate", cert_expired=true)` →
returns only `CN=api.example.com`.
**Output:** `"One expired certificate was found: CN=api.example.com (issuer Let's Encrypt,
expired 2025-01-02), which covers api.example.com."`

**Prompt:** `"What's the risk of the service 3389/tcp?"`
**Behind the scenes:** `score_asset_risk("3389/tcp")` with signal `sensitive_service=true`.
**Output (structured `RiskAssessment`):** `{"risk_score": 80, "summary": "Exposed RDP
(3389/tcp) on a production host — high-value target for brute-force and lateral movement.",
"factors": ["sensitive port 3389 (RDP)", "production tag"]}`

**Prompt:** `"Classify and enrich api.example.com."`
**Output:** persisted enrichment `{"environment":"prod","category":"api","criticality":"high"}`
written into the asset's metadata (visible afterward via `GET /api/v1/assets`).

**Prompt:** `"Does foo.invalid.example exist?"` (asset not in inventory)
**Output:** `"I cannot find foo.invalid.example in the database."` (no fabrication.)

## Design decisions & assumptions

- **Async stack.** SQLAlchemy async engine + `asyncpg` so DB calls don't block the event loop.
- **Conflict merge strategy.** Re-importing an asset merges, it doesn't clobber: metadata
  is a key-wise merge (newer source wins per key, older keys retained) and tags are a
  de-duplicated **union** (`crud.merge_metadata` / `merge_tags`, unit-tested). `first_seen`
  is set once; `last_seen` updates on every re-sighting.
- **Idempotency.** Asset upsert keys on the composite PK `(id, org_id)`. Relationship edges
  use a **deterministic `uuid5`** of `(org, source, target, type)` plus a unique constraint,
  so re-imports never create duplicate edges (the previous random-UUID approach did).
- **Re-appearing assets** revive: a stale asset seen again returns to `active`.
- **Malformed records** are validated per row (`AssetImport`); a bad record is reported in
  `errors` and counted as failed without aborting the batch.
- **Lifecycle dates in code, not the model.** Expired / expiring-soon and EOL-tech are
  computed deterministically (`lifecycle.py`); the LLM only narrates them.
- **Structured output.** Risk and enrichment use `llm.with_structured_output(...)` against
  Pydantic schemas, so those tool results are validated, not free-text.
- **Security.** Keys never logged; agent `verbose` is off by default; both write and paid
  endpoints require auth; per-org rate limiting on `/analyze`; batch-size cap; `.dockerignore`
  keeps local `.env` out of the image; secrets are env-driven with placeholders in `.env.example`.
- **Schema management.** `create_all` runs on startup for demo convenience; **Alembic
  migrations** are the intended production path.

## Edge cases handled

Idempotent imports · conflicting two-source data (merge) · stale→active revival ·
malformed/partial records (graceful, per-row) · large lists (pagination + capped query/report
with an explicit "showing N of M" note) · certificate expired vs expiring-soon · ambiguous /
out-of-scope queries (agent returns "not found"/asks to clarify, never invents) · multi-tenant
isolation (org from API key).

## Tests

```bash
# Pure unit tests (no DB or API key needed): merge/dedup, lifecycle dates, validation, auth
pytest tests/test_merge.py tests/test_lifecycle.py tests/test_schemas.py tests/test_auth.py

# Full suite incl. DB integration (dedup, merge, idempotent relationships, isolation, filtering)
docker compose exec web pytest
# or locally with a reachable Postgres:
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/darkatlas_asm pytest
```

The integration tests skip cleanly if no PostgreSQL is reachable, so the pure suite always runs.

## What I'd do next

- Alembic migrations instead of `create_all`; richer relationship typing in the import schema
  (explicit `service→ip`, `technology→service`).
- Output-quality eval harness + LLM response caching (bonus).
- Roles/scopes on top of API keys; shared-store rate limiting for multi-instance deploys.
- A small graph visualization of the relationships endpoint.
