from __future__ import annotations

import json
import re
from typing import Any, Protocol

from backend.app.core.config import settings

ADVISORY_NOTE = (
    "Suggestions only. The verified deletion status is computed by deterministic "
    "application code from persisted check evidence and is never changed by this output."
)

ALLOWED_STEP_ACTIONS = frozenset(
    {
        "inspect_check",
        "review_evidence",
        "retry_operation_via_api",
        "inspect_cache_entries",
        "inspect_vector_collection",
        "verify_configuration",
        "escalate_to_operator",
    }
)

_ERROR_CATEGORY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")


class ExplanationError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class ExplanationProvider(Protocol):
    """Small provider interface. Implementations return raw model text."""

    name: str

    def explain(self, evidence: dict[str, Any]) -> str:
        ...


class LiteLLMExplanationProvider:
    """Real provider via litellm. Constructed only when a key is configured."""

    name = "litellm"

    def __init__(self, model: str | None = None, timeout_seconds: int = 20) -> None:
        self.model = model or settings.litellm_model
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _credentials_for(model: str) -> tuple[str | None, str | None]:
        if model.startswith("groq/"):
            return settings.groq_api_key, None
        return settings.litellm_api_key, settings.litellm_api_base

    def explain(self, evidence: dict[str, Any]) -> str:
        try:
            import litellm
        except Exception as error:
            raise ExplanationError("ProviderUnavailable") from error
        api_key, api_base = self._credentials_for(self.model)
        if not api_key:
            raise ExplanationError("ProviderUnavailable")
        try:
            response = litellm.completion(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You explain deletion-verification reports for operators. "
                            "Never claim deletion succeeded, failed, or is verified: "
                            "status is decided elsewhere. Suggest only inspection and "
                            "evidence-gathering steps. Reply with JSON only: an object "
                            "with optional 'summary' (string), optional 'likely_causes' "
                            "(array of strings), and optional 'suggested_steps' (array "
                            "of objects with 'action', 'detail', and optional 'check_id')."
                        ),
                    },
                    {"role": "user", "content": json.dumps(evidence, sort_keys=True)},
                ],
                api_key=api_key,
                api_base=api_base,
                temperature=0,
                timeout=self.timeout_seconds,
                max_tokens=600,
            )
        except Exception as error:
            raise ExplanationError(type(error).__name__) from error
        try:
            content = response.choices[0].message.content
        except Exception as error:
            raise ExplanationError("MalformedProviderResponse") from error
        if not content or not content.strip():
            raise ExplanationError("EmptyProviderResponse")
        return content


def _strip_fences(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*\n", "", cleaned)
        cleaned = re.sub(r"\n```\s*$", "", cleaned)
    return cleaned.strip()


def _error_category(error_detail: str | None) -> str | None:
    if not error_detail:
        return None
    candidate = error_detail.split(":", 1)[0].strip()
    return candidate if _ERROR_CATEGORY.fullmatch(candidate) else "Unknown"


def build_evidence(report: dict[str, Any]) -> dict[str, Any]:
    """Privacy-minimized evidence: IDs, layers, methods, statuses, counts.

    Never includes document text, chunk text, embeddings, query text, secrets,
    tokens, chunk IDs, or file paths -- only operational metadata.
    """
    checks = []
    for check in report.get("checks", []):
        actual = check.get("actual_result") or {}
        residual_count = actual.get("residual_count")
        checks.append(
            {
                "check_id": check.get("check_id"),
                "layer": check.get("layer"),
                "method": check.get("method"),
                "status": check.get("status"),
                "error_category": _error_category(check.get("error_detail")),
                "residual_count": residual_count if isinstance(residual_count, int) else None,
            }
        )
    counts: dict[str, int] = {}
    for check in checks:
        counts[check["status"]] = counts.get(check["status"], 0) + 1
    return {
        "operation_id": report.get("operation_id"),
        "document_id": report.get("document_id"),
        "verified_status": report.get("status"),
        "check_count": len(checks),
        "status_counts": counts,
        "checks": checks,
    }


def _validate_steps(raw_steps: Any, known_check_ids: set[str]) -> tuple[list[dict[str, Any]], int]:
    steps: list[dict[str, Any]] = []
    dropped = 0
    if not isinstance(raw_steps, list):
        return steps, dropped
    for raw in raw_steps[:10]:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        action = raw.get("action")
        detail = raw.get("detail")
        check_id = raw.get("check_id")
        if action not in ALLOWED_STEP_ACTIONS or not isinstance(detail, str) or not detail.strip():
            dropped += 1
            continue
        if check_id is not None and check_id not in known_check_ids:
            dropped += 1
            continue
        step: dict[str, Any] = {"action": action, "detail": detail.strip()[:500]}
        if check_id is not None:
            step["check_id"] = check_id
        steps.append(step)
    return steps, dropped


def build_explanation(
    report: dict[str, Any], provider: ExplanationProvider | None
) -> dict[str, Any]:
    """Advisory explanation. The input verified status is echoed unchanged.

    Any `status`/`verdict`-like fields in model output are ignored: model text
    can never upgrade or downgrade the deterministic result.
    """
    evidence = build_evidence(report)
    verified_status = report.get("status")
    base: dict[str, Any] = {
        "operation_id": report.get("operation_id"),
        "verified_status": verified_status,
        "advisory_only": True,
        "note": ADVISORY_NOTE,
        "evidence_check_ids": [check["check_id"] for check in evidence["checks"]],
    }
    if provider is None:
        return {
            **base,
            "outcome": "unavailable",
            "provider": None,
            "summary": None,
            "likely_causes": [],
            "suggested_steps": [],
            "dropped_unsafe_steps": 0,
            "detail": "No AI explanation provider is configured. Deterministic verification reports work without it.",
        }
    try:
        raw_text = provider.explain(evidence)
    except ExplanationError as error:
        category = error.category
    except Exception as error:
        category = type(error).__name__
    else:
        category = None
    if category is not None:
        return {
            **base,
            "outcome": "degraded",
            "provider": {"name": getattr(provider, "name", "unknown")},
            "summary": None,
            "likely_causes": [],
            "suggested_steps": [],
            "dropped_unsafe_steps": 0,
            "detail": f"Explanation provider did not return usable output ({category}).",
        }
    try:
        parsed = json.loads(_strip_fences(raw_text))
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        return {
            **base,
            "outcome": "degraded",
            "provider": {"name": getattr(provider, "name", "unknown")},
            "summary": None,
            "likely_causes": [],
            "suggested_steps": [],
            "dropped_unsafe_steps": 0,
            "detail": "Explanation provider returned malformed output.",
        }
    summary = parsed.get("summary")
    causes = parsed.get("likely_causes")
    steps, dropped = _validate_steps(
        parsed.get("suggested_steps"), set(base["evidence_check_ids"])
    )
    return {
        **base,
        "outcome": "ok",
        "provider": {"name": getattr(provider, "name", "unknown")},
        "summary": summary.strip()[:1000] if isinstance(summary, str) and summary.strip() else None,
        "likely_causes": [item.strip()[:300] for item in causes[:5] if isinstance(item, str) and item.strip()]
        if isinstance(causes, list)
        else [],
        "suggested_steps": steps,
        "dropped_unsafe_steps": dropped,
        "detail": None,
    }
