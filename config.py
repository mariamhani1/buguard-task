"""Central configuration. All settings come from environment variables (loaded from
.env for local runs); nothing secret is hard-coded. See .env.example for the full list."""
import os
import logging

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("darkatlas")

# --- Database ---------------------------------------------------------------
# A dev default is provided so the test suite and `docker compose` work out of the
# box; production must supply DATABASE_URL explicitly.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@db:5432/darkatlas_asm",
)

# --- LLM --------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
AGENT_VERBOSE = os.getenv("AGENT_VERBOSE", "false").lower() in ("1", "true", "yes")
AGENT_MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "8"))

# --- Behaviour limits -------------------------------------------------------
MAX_IMPORT_BATCH = int(os.getenv("MAX_IMPORT_BATCH", "5000"))
ANALYZE_RATE_LIMIT_PER_MIN = int(os.getenv("ANALYZE_RATE_LIMIT_PER_MIN", "30"))
EXPIRING_SOON_DAYS = int(os.getenv("EXPIRING_SOON_DAYS", "30"))
QUERY_DEFAULT_LIMIT = int(os.getenv("QUERY_DEFAULT_LIMIT", "50"))
QUERY_MAX_LIMIT = int(os.getenv("QUERY_MAX_LIMIT", "200"))
REPORT_MAX_ASSETS = int(os.getenv("REPORT_MAX_ASSETS", "500"))

# Ports that are sensitive when exposed to the internet, used by deterministic
# risk signals (RDP, SMB, Telnet, FTP, databases, etc.).
SENSITIVE_PORTS = {21, 23, 25, 110, 135, 139, 445, 1433, 1521, 3306, 3389, 5432, 5900, 6379, 9200, 27017}

# Technologies considered end-of-life regardless of version (lowercased name -> note).
KNOWN_EOL_TECH = {
    "openssl 1.0.2": "OpenSSL 1.0.x reached end-of-life in 2019",
    "python 2.7": "Python 2 reached end-of-life in 2020",
    "php 5.6": "PHP 5.x is end-of-life",
    "windows server 2008": "Windows Server 2008 is end-of-life",
    "apache 2.2": "Apache httpd 2.2 is end-of-life",
}


def _parse_api_keys() -> dict[str, str]:
    """Parse API_KEYS env var of the form 'key1:org1,key2:org2' into {key: org_id}.

    The API key is the trust anchor: the org a caller may act on is derived from
    their key server-side, never from a client-supplied header."""
    raw = os.getenv("API_KEYS", "").strip()
    keys: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, org = pair.split(":", 1)
        if key.strip() and org.strip():
            keys[key.strip()] = org.strip()
    if not keys:
        # Built-in DEV keys so the stack is runnable immediately. Never use in prod.
        keys = {"dev-key-acme": "org_acme", "dev-key-globex": "org_globex"}
        logger.warning(
            "API_KEYS not configured; falling back to built-in DEV keys "
            "(dev-key-acme/org_acme, dev-key-globex/org_globex). Do NOT use in production."
        )
    return keys


API_KEYS = _parse_api_keys()
