"""LangChain analysis layer.

Five grounded tools wrap the service layer (each opening its own short-lived DB
session) and expose the four mandatory capabilities:
  * search_assets            -> natural-language asset query (rich, structured filters)
  * score_asset_risk         -> risk scoring & summarization (structured output)
  * enrich_asset             -> enrichment & categorization (persisted, structured output)
  * generate_inventory_report-> natural-language report generation (grounded context)
  * get_asset_graph          -> relationship graph traversal (supports the others)

Grounding is enforced three ways: tools only ever return real DB rows, deterministic
risk facts are computed in Python (lifecycle), and a strict system prompt forbids the
model from inventing assets."""
import json
import logging
from typing import List, Optional

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

import config
import lifecycle
import services
from schemas import RiskAssessment, Enrichment

logger = logging.getLogger("darkatlas")

_llm: Optional[ChatOpenAI] = None


def get_llm() -> ChatOpenAI:
    """Build the chat model once and reuse it across requests."""
    global _llm
    if _llm is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        _llm = ChatOpenAI(model=config.LLM_MODEL, temperature=0, api_key=config.OPENAI_API_KEY)
    return _llm


SYSTEM_PROMPT = (
    "You are the Buguard DarkAtlas attack-surface analysis assistant. "
    "Answer ONLY using data returned by your tools. "
    "NEVER invent assets, domains, IPs, certificates, ports, or dates that are not present in tool output. "
    "If a tool returns 'not found' or no results, say so plainly and do not fabricate. "
    "When a question is ambiguous or outside the asset inventory, ask for clarification or state it is out of scope. "
    "Prefer concise, factual answers grounded in the retrieved rows."
)


def get_analysis_agent(session_factory, org_id: str) -> AgentExecutor:
    """Construct an org-scoped tool-calling agent. `session_factory` is the async
    sessionmaker; each tool opens its own session so no session is shared across the
    agent run."""
    llm = get_llm()

    @tool
    async def search_assets(
        asset_type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
        value_contains: Optional[str] = None,
        cert_expired: Optional[bool] = None,
        expiring_within_days: Optional[int] = None,
    ) -> str:
        """Find assets in the inventory. Filters (all optional, combinable):
        asset_type (domain/subdomain/ip_address/service/certificate/technology),
        status (active/stale/archived), tags (match any, e.g. ['prod','production']),
        value_contains (substring of the asset value), cert_expired (true=only expired
        certificates), expiring_within_days (certificates expiring within N days)."""
        async with session_factory() as db:
            items, total = await services.list_assets(
                db, org_id,
                type=asset_type, status=status, tags=tags, value_contains=value_contains,
                cert_expired=cert_expired, expiring_within_days=expiring_within_days,
            )
        if not items:
            return "No assets found matching that criteria."
        note = f"\n(Showing {len(items)} of {total} matches.)" if total > len(items) else ""
        return json.dumps(items, default=str) + note

    @tool
    async def score_asset_risk(asset_value: str) -> str:
        """Compute a risk score (0-100) and concise summary for a single asset,
        identified by its exact value (e.g. 'api.example.com' or '3389/tcp')."""
        async with session_factory() as db:
            asset = await services.get_asset_by_value(db, org_id, asset_value)
        if not asset:
            return f"Asset '{asset_value}' not found in the database."
        signals = lifecycle.asset_risk_signals(asset, config.EXPIRING_SOON_DAYS)
        prompt = ChatPromptTemplate.from_template(
            "Assess the security risk of this asset using ONLY the provided data and the "
            "precomputed deterministic signals. Today is {today}.\n"
            "Asset: {asset}\nSignals: {signals}"
        )
        chain = prompt | llm.with_structured_output(RiskAssessment)
        result: RiskAssessment = await chain.ainvoke({
            "today": lifecycle.now_utc().date().isoformat(),
            "asset": json.dumps(asset, default=str),
            "signals": json.dumps(signals, default=str),
        })
        return result.model_dump_json()

    @tool
    async def enrich_asset(asset_value: str) -> str:
        """Classify and enrich a single existing asset (environment, category,
        criticality) and PERSIST the result into its metadata. Identify it by value."""
        async with session_factory() as db:
            asset = await services.get_asset_by_value(db, org_id, asset_value)
            if not asset:
                return f"Asset '{asset_value}' not found; cannot enrich a non-existent asset."
            prompt = ChatPromptTemplate.from_template(
                "Classify this asset's environment (prod/staging/dev/unknown), functional "
                "category, and criticality using ONLY its data.\nAsset: {asset}"
            )
            chain = prompt | llm.with_structured_output(Enrichment)
            enrichment: Enrichment = await chain.ainvoke({"asset": json.dumps(asset, default=str)})
            updated = await services.apply_enrichment(db, org_id, asset_value, enrichment.model_dump())
        return json.dumps({"persisted": True, "asset": updated}, default=str)

    @tool
    async def generate_inventory_report() -> str:
        """Generate a readable inventory and risk report for the whole organization,
        grounded in a deterministically-computed risk context."""
        async with session_factory() as db:
            context = await services.build_report_context(db, org_id, config.EXPIRING_SOON_DAYS)
        if not context["assets"]:
            return "Cannot generate report: no assets exist for this organization."
        prompt = ChatPromptTemplate.from_template(
            "You are a CISO writing a brief inventory & risk report. Use ONLY this grounded "
            "context (counts, expired/expiring certificates, sensitive exposed services, "
            "end-of-life technologies). Do not invent any asset, host, or finding not present "
            "in the context.\n\nContext (JSON):\n{context}"
        )
        chain = prompt | llm
        result = await chain.ainvoke({"context": json.dumps(context, default=str)})
        return result.content

    @tool
    async def get_asset_graph(asset_value: str) -> str:
        """Return an asset together with its related assets (subdomain->domain,
        service->ip, certificate->subdomain, etc.). Identify it by value."""
        async with session_factory() as db:
            graph = await services.get_neighbors(db, org_id, asset_value)
        if not graph:
            return f"Asset '{asset_value}' not found in the database."
        return json.dumps(graph, default=str)

    tools = [search_assets, score_asset_risk, enrich_asset, generate_inventory_report, get_asset_graph]

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=config.AGENT_VERBOSE,
        handle_parsing_errors=True,
        max_iterations=config.AGENT_MAX_ITERATIONS,
    )
