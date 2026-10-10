from __future__ import annotations

from typing import Any

from backend.app.integrations.neatlogs import emit_trace


def send_trace(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return emit_trace(event_name, payload)
