from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(PROJECT_DIR / ".env", override=False)


class Settings:
    app_name = "GhostErase"
    api_v1_prefix = "/api/v1"
    demo_mode = os.getenv("GHOSTERASE_DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}
    database_url = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'ghosterase.sqlite3').as_posix()}")
    upload_dir = Path(os.getenv("GHOSTERASE_UPLOAD_DIR", str(DATA_DIR / "uploads")))
    max_upload_bytes = 5 * 1024 * 1024
    delete_auth_token = os.getenv("DELETE_AUTH_TOKEN")
    chroma_dir = Path(os.getenv("CHROMA_DIR", str(DATA_DIR / "chroma")))
    chroma_collection = "ghosterase_documents"
    neatlogs_api_key = os.getenv("NEATLOGS_API_KEY")
    neatlogs_endpoint = os.getenv("NEATLOGS_ENDPOINT", "https://ingest.neatlogs.com").rstrip("/")
    litellm_api_key = os.getenv("LITELLM_API_KEY")
    litellm_api_base = os.getenv("LITELLM_API_BASE", "https://litellm.vision.aivar.app/v1")
    litellm_model = os.getenv("LITELLM_MODEL", "openai/claude-haiku")
    groq_api_key = os.getenv("GROQ_API_KEY")


settings = Settings()
