from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


class Settings:
    app_name = "GhostErase"
    data_dir = BASE_DIR / "data"
    chroma_dir = data_dir / "chroma"
    db_path = data_dir / "ghosterase.sqlite3"

    litellm_api_key = os.getenv("LITELLM_API_KEY")
    litellm_api_base = os.getenv("LITELLM_API_BASE", "https://api.openai.com/v1")
    litellm_model = os.getenv("LITELLM_MODEL", "openai/gpt-4o-mini")
    litellm_embedding_model = os.getenv("LITELLM_EMBEDDING_MODEL", "text-embedding-3-small")

    neatlogs_api_key = os.getenv("NEATLOGS_API_KEY")
    neatlogs_endpoint = os.getenv("NEATLOGS_ENDPOINT", "https://ingest.neatlogs.com").rstrip("/")

    cfo_api_base = os.getenv("CFO_API_BASE", "https://api.cfo.ai/mcp")


settings = Settings()
