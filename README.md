# Buguard DarkAtlas - Asset Management API (AI Applications Track)

This repository contains the completed Internship Task for Track B. It is a self-contained module of the DarkAtlas ASM platform, built with FastAPI, PostgreSQL, and LangChain.

## 🚀 Features & Rubric Highlights
* **Agentic Tool-Use (Bonus):** Instead of rigid, hardcoded LLM chains, a LangChain ReAct agent orchestrates four distinct capabilities (Query, Risk Scoring, Enrichment, Report Generation) dynamically based on natural language input.
* **Idempotent Ingestion:** The `/import` endpoint safely handles partial data, deduplicates records, and automatically manages the `last_seen` and `status` lifecycles using strict PostgreSQL `ON CONFLICT` UPSERTs.
* **Multi-Tenant Isolation (Bonus):** Enforced data scoping using the `X-Organization-ID` header ensures asset data never leaks between distinct organizations.
* **Strict Guardrails:** The agent is prompted strictly to prevent hallucinating domains or IP addresses that do not exist in the database context.

## 🛠 Setup & Run Instructions

1. **Environment Variables:**
   Create a `.env` file in the root directory based on the provided `.env.example`:
   `OPENAI_API_KEY=your_active_api_key_here`
   `DATABASE_URL=postgresql+asyncpg://postgres:securepassword123@db:5432/darkatlas_asm`

2. **Launch the Infrastructure:**
   The application and database are fully containerized. Start them using Docker Compose:
   `docker-compose up --build -d`

3. **Access the API:**
   Navigate to the Swagger UI to test the endpoints: **http://localhost:8000/docs**

## 🤖 Example Prompts & Outputs

**Endpoint:** `POST /api/v1/analyze`
**Prompt:** `"Show me all the active domains we have."`
**Expected Agent Output:**
{
  "result": "Based on the current inventory, we have the following active domains:\n\n1. **example.com** (ID: a1, Type: domain, Status: active)\n\nLet me know if you would like a risk assessment on this asset."
}

## 🧠 Design Decisions & Assumptions
* **Async SQLAlchemy:** Used `asyncpg` to ensure the web server is non-blocking during database calls, critical for a high-throughput ASM environment.
* **JSONB vs Relational:** Kept `metadata` as a PostgreSQL JSONB column to allow dynamic schema ingestion from various scan types, while strict relational columns handle mandatory filtering fields.
* **LangChain Versioning:** Pinned LangChain to `>=0.1.20` to utilize the modern `create_tool_calling_agent` for stable JSON parsing and tool orchestration.