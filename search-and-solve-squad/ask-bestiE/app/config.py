import os
from dotenv import load_dotenv

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "local")
ALLOWED_LINK_PREFIXES = [p.strip() for p in os.getenv("ALLOWED_LINK_PREFIXES", "").split(",") if p.strip()]

DATABRICKS_HOST = os.getenv("DATABRICKS_HOST", "")
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")
VECTOR_SEARCH_INDEX_NAME = os.getenv("VECTOR_SEARCH_INDEX_NAME", "help_docs_index")
PHI3_ENDPOINT = os.getenv("PHI3_ENDPOINT", "")

PORT = int(os.getenv("PORT", "3000"))
SKIP_SLACK_SIGNATURE_VERIFY = os.getenv("SKIP_SLACK_SIGNATURE_VERIFY", "false").lower() == "true"
