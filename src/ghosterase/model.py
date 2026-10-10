from __future__ import annotations

from typing import Any

try:
    import litellm
except Exception:  # pragma: no cover
    litellm = None

from .config import settings
from backend.app.integrations.telemetry import telemetry_span


class HostedModelClient:
    def __init__(self) -> None:
        self.api_key = settings.litellm_api_key
        self.api_base = settings.litellm_api_base
        self.model = settings.litellm_model
        self.embedding_model = settings.litellm_embedding_model
        self.connected = self.verify_connection()

    def verify_connection(self) -> bool:
        if not self.api_key or litellm is None:
            return False
        try:
            with telemetry_span(
                "model.completion",
                kind="LLM",
                operation_type="model_connection_check",
                model_identifier=self.model,
            ) as span:
                response = litellm.completion(
                    model=self.model,
                    messages=[{"role": "user", "content": "Reply with the word READY."}],
                    api_key=self.api_key,
                    api_base=self.api_base,
                    temperature=0,
                )
                connected = bool(getattr(response, "choices", None))
                span.set_result("pass" if connected else "fail")
                return connected
        except Exception:
            return False

    def generate_text(self, prompt: str) -> str | None:
        if not self.api_key or litellm is None:
            return None
        try:
            with telemetry_span(
                "model.completion",
                kind="LLM",
                operation_type="grounded_generation",
                model_identifier=self.model,
            ) as span:
                response = litellm.completion(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    api_key=self.api_key,
                    api_base=self.api_base,
                    temperature=0,
                )
                output = getattr(response.choices[0].message, "content", None)
                span.set_result("pass" if output else "fail")
                return output
        except Exception:
            return None

    def embed_text(self, text: str) -> list[float] | None:
        if not self.api_key or litellm is None:
            return None
        try:
            with telemetry_span(
                "rag.embedding",
                kind="EMBEDDING",
                operation_type="hosted_embedding",
                model_identifier=self.embedding_model,
            ) as span:
                response = litellm.embedding(
                    model=self.embedding_model,
                    input=text,
                    api_key=self.api_key,
                    api_base=self.api_base,
                )
                payload = getattr(response, "data", None) or []
                if not payload:
                    span.set_result("fail")
                    return None
                embedding = payload[0].get("embedding")
                span.set_result("pass" if embedding else "fail")
                return embedding
        except Exception:
            return None
