# Buguard DarkAtlas - Asset Management API

This repository is my submission for the Buguard DarkAtlas Attack Surface Monitoring Internship Task, Track B: AI Applications. It is a self-contained Asset Management API built with Python, FastAPI, PostgreSQL, and LangChain.

## Overview & Features

The service provides a focused backend for attack surface monitoring, with a single AI analysis endpoint that can interrogate inventory data, score risk, enrich assets, and generate reports from grounded database context.

The implementation covers all four mandatory LangChain capabilities required by the rubric:

* Natural-language querying of the asset inventory.
* Risk scoring and summarization for a specific asset.
* Automated enrichment to infer environment and criticality.
* Natural-language report generation for the overall organization.

It also includes the two bonus stretch goals:

* Agentic tool-use, implemented with a LangChain ReAct-style tool-calling agent that selects the appropriate capability dynamically from user intent.
* Multi-tenant isolation, enforced through the `X-Organization-ID` header so each organization only sees its own data.

Additional implementation protections include idempotent ingestion, deduplication, and strict anti-hallucination guardrails so the model only responds from retrieved database context.

## Setup & Run Instructions

1. Clone the repository and enter the project directory.

```bash
git clone <your-repo-url>
cd buguard-task
```

2. Create a `.env` file in the project root based on `.env.example`.

```env
OPENAI_API_KEY=your_active_api_key_here
DATABASE_URL=postgresql+asyncpg://postgres:securepassword123@db:5432/darkatlas_asm
```

Never commit real secrets, API keys, or production database credentials to the repository.

3. Launch the application and PostgreSQL together with Docker Compose.

```bash
docker-compose up --build -d
```

The API will be available at `http://localhost:8000`, and the PostgreSQL container will be started at the same time through the `db` service.

## API Documentation & Testing

The auto-generated Swagger UI is available at:

`http://localhost:8000/docs`

To run the test suite inside the application container, use:

```bash
docker-compose exec web pytest
```

If no tests are present yet, this command should be used once a `pytest` suite is added to the project.

## Example Prompts & Outputs

### 1) Active Domains Query

**Endpoint:** `POST /api/v1/analyze`

**Request**

```json
{
   "prompt": "Show me all active domains in our inventory."
}
```

**Response**

```json
{
   "result": "Active domains currently found in the database:\n\n1. example.com (ID: a1, Status: active)\n2. api.example.net (ID: a2, Status: active)\n\nI can also summarize the risk posture for any of these assets if needed."
}
```

### 2) Certificate Risk Scoring

**Endpoint:** `POST /api/v1/analyze`

**Request**

```json
{
   "prompt": "Generate a risk score for the certificate attached to portal.example.com that expires in 5 days."
}
```

**Response**

```json
{
   "result": "Risk score: 92/100.\n\nSummary: The certificate is close to expiration and presents a high likelihood of service disruption if not renewed promptly. Assets depending on this endpoint should be reviewed for failover or renewal readiness."
}
```

## Design Decisions & Assumptions

* `asyncpg` is used through SQLAlchemy’s async engine so database calls do not block the FastAPI event loop. This keeps the API responsive during ingestion and AI analysis workloads.
* `metadata` is stored as a PostgreSQL `JSONB` column so the system can accept dynamic scan payloads without forcing a rigid relational schema for every enrichment source.
* Bulk imports are idempotent by design. The import pipeline uses PostgreSQL `ON CONFLICT` UPSERT statements to deduplicate records by `(id, org_id)` and safely update existing rows instead of inserting duplicates.
* When duplicate records arrive, the newest scan is treated as the source of truth. The import flow refreshes `last_seen`, updates the asset back to `active`, and replaces stored metadata with the latest payload.
* Re-appearing assets are intentionally cycled back to `active` during import so stale inventory can be revived automatically when the same asset is observed again.
* Malformed records are isolated inside `try/except` blocks during bulk import. A bad row is counted as a failure, but it does not abort the entire batch, which allows partial success and better operational resilience.
* The LangChain agent is protected by a strict system prompt that forces grounding in tool output only. It is instructed not to hallucinate domains, IP addresses, or other assets that are not present in the database.
* The AI layer is intentionally constrained to the known inventory context rather than unconstrained free-form generation. If the database does not contain the answer, the assistant should explicitly say so.

## Repository Notes

* FastAPI serves the API layer and automatically exposes the OpenAPI schema and Swagger UI.
* PostgreSQL stores the asset inventory, relationships, and tenant-scoped records.
* LangChain orchestrates the four analysis capabilities through a tool-calling agent.
* Docker Compose brings up both the web application and the database in one command for reproducible local setup.
