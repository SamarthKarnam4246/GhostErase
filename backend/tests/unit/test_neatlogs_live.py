from __future__ import annotations

"""Live Neatlogs verification protocol (external, credential-gated).

These tests require a working NEATLOGS_API_KEY and network access, so they
are skipped by default. Run with NEATLOGS_LIVE=1 to execute them; never
substitute fakes and never report them passed without running them.

Protocol: authenticated doctor probe, synthetic app workflow against a local
backend started with the key, then per-trace readback inspection proving the
canary phrase and other prohibited content are absent from exported fields.
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.external

requires_live = pytest.mark.skipif(
    os.getenv("NEATLOGS_LIVE") != "1",
    reason="requires NEATLOGS_API_KEY and network access (set NEATLOGS_LIVE=1)",
)


@requires_live
def test_live_probe_roundtrip() -> None:
    import json
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "neatlogs", "doctor", "--probe", "--json"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout)["status"] == "pass"


@requires_live
def test_live_workflow_trace_has_no_prohibited_content() -> None:
    """Placeholder for the per-trace readback step.

    Fill in once a working key is available: run the synthetic workflow,
    fetch each emitted trace ID with `neatlogs trace get`, and assert the
    canary phrase, document bodies, tokens, and embeddings are absent while
    workflow names and allowlisted metadata are present.
    """
    pytest.skip("no verified trace IDs yet: readback tooling needs NEATLOGS_TOKEN")
    _ = f"workflow-{uuid.uuid4().hex}"
