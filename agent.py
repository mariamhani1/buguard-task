import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from langchain.agents import tool, AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from models import Asset
from tabulate import tabulate

def get_analysis_agent(db: AsyncSession, org_id: str):
    # Initialize the LLM (ensure OPENAI_API_KEY is in your .env)
    llm = ChatOpenAI(temperature=0, model="gpt-4o-mini")

    # --- TOOL 1: Natural Language Query ---
    @tool
    async def query_assets_tool(asset_type: str = None, status: str = None) -> str:
        """Use this to find specific assets. You can filter by asset_type (e.g., domain, certificate) or status (e.g., active, stale)."""
        query = select(Asset).where(Asset.org_id == org_id)
        if asset_type:
            query = query.where(Asset.type == asset_type)
        if status:
            query = query.where(Asset.status == status)
            
        result = await db.execute(query.limit(50)) # Sane limits to protect context window
        assets = result.scalars().all()
        
        if not assets:
            return "No assets found matching that criteria."
            
        data = [{"id": a.id, "value": a.value, "type": a.type, "status": a.status.value} for a in assets]
        return json.dumps(data)

    # --- TOOL 2: Risk Scoring & Summarization ---
    @tool
    async def calculate_risk_tool(asset_value: str) -> str:
        """Use this to calculate a risk score and summarize vulnerabilities for a specific asset by its value (e.g., example.com)."""
        query = select(Asset).where(and_(Asset.org_id == org_id, Asset.value == asset_value))
        result = await db.execute(query)
        asset = result.scalars().first()
        
        if not asset:
            return f"Asset {asset_value} not found in the database."
            
        # Have the LLM analyze the metadata internally
        prompt = ChatPromptTemplate.from_template(
            "Analyze this asset for security risks (e.g., expired certs, exposed ports). "
            "Return a strict JSON with a numerical 'risk_score' (0-100) and a short 'summary'.\n\nAsset Data: {data}"
        )
        chain = prompt | llm
        res = await chain.ainvoke({"data": json.dumps(asset.metadata_)})
        return res.content

    # --- TOOL 3: Automated Enrichment ---
    @tool
    def enrich_asset_tool(asset_value: str, raw_tags: list[str]) -> str:
        """Use this to classify the environment (prod/staging/dev) and criticality of an asset based on its value and tags."""
        prompt = ChatPromptTemplate.from_template(
            "Given the asset '{value}' and tags '{tags}', classify its environment (prod, staging, dev) "
            "and criticality (low, medium, high). Return ONLY a JSON object with these two keys."
        )
        chain = prompt | llm
        res = chain.invoke({"value": asset_value, "tags": raw_tags})
        return res.content

    # --- TOOL 4: Natural Language Report Generation ---
    @tool
    async def generate_report_tool() -> str:
        """Use this to generate a comprehensive markdown-formatted inventory and risk report for the entire organization."""
        query = select(Asset).where(Asset.org_id == org_id)
        result = await db.execute(query)
        assets = result.scalars().all()
        
        if not assets:
            return "Cannot generate report: No assets exist in the database."
            
        rows = [[a.type, a.value, a.status.value] for a in assets]
        table_md = tabulate(rows, headers=["Type", "Value", "Status"], tablefmt="github")
        
        prompt = ChatPromptTemplate.from_template(
            "You are a CISO. Write a brief executive summary based on this asset inventory table:\n\n{table}"
        )
        chain = prompt | llm
        res = await chain.ainvoke({"table": table_md})
        return res.content

    tools = [query_assets_tool, calculate_risk_tool, enrich_asset_tool, generate_report_tool]
    
    # Grounding Guardrail
    system_prompt = (
        "You are the Buguard DarkAtlas AI Assistant. "
        "Strict Rule: Answer questions based ONLY on data retrieved from your tools. "
        "NEVER hallucinate assets, domains, or IP addresses that do not exist in the database. "
        "If you do not know the answer, say 'I cannot find this information in the database.'"
    )
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])
    
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)