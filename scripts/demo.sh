#!/usr/bin/env bash
# GhostErase synthetic demo (local only, synthetic data only).
# Exercises the live legs against an isolated backend; the stale-cache
# residual leg runs as a deterministic integration test because the app
# exposes no test hooks to plant stale rows (by design).
# Exits non-zero on any failed assertion. Never prints secrets or the canary
# anywhere except local variables used for same-process assertions.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR=""
BACKEND_PID=""

fail() { echo "FAIL - $1" >&2; exit 1; }
ok() { echo "ok - $1"; }

cleanup() {
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null
    wait "$BACKEND_PID" 2>/dev/null
  fi
  if [ -n "$DATA_DIR" ] && [ -d "$DATA_DIR" ]; then
    rm -rf "$DATA_DIR"
  fi
}
trap cleanup EXIT

command -v curl >/dev/null || fail "curl is required"
command -v jq >/dev/null || fail "jq is required"
command -v python3 >/dev/null || fail "python3 is required"
[ -n "${DELETE_AUTH_TOKEN:-}" ] || fail "DELETE_AUTH_TOKEN must be set in the environment (never committed)"
[ -x "$REPO/.venv/bin/python" ] || fail "backend venv missing at $REPO/.venv (install requirements first)"

DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ghosterase-demo.XXXXXX")"
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
export DATABASE_URL="sqlite:///${DATA_DIR}/metadata.sqlite3"
export GHOSTERASE_UPLOAD_DIR="${DATA_DIR}/uploads"
export CHROMA_DIR="${DATA_DIR}/chroma"
export NEATLOGS_API_KEY=""
export LITELLM_API_KEY=""
export GHOSTERASE_DEMO_MODE="true"
export PYTHONPATH="$REPO"

start_backend() {
  (cd "$REPO" && exec .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT" >/dev/null 2>&1) &
  BACKEND_PID=$!
}

wait_for_health() {
  for _ in $(seq 1 120); do
    if curl -sf "http://127.0.0.1:${PORT}/api/v1/health" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      fail "backend exited during startup"
    fi
    sleep 1
  done
  fail "backend did not become healthy"
}

BASE="http://127.0.0.1:${PORT}/api/v1"

start_backend
wait_for_health
ok "isolated backend healthy on loopback"

MARKER="ghost-erase-demo-$(date +%s)-${RANDOM}${RANDOM}"
DOC_FILE="${DATA_DIR}/synthetic-demo.txt"
{
  printf 'Synthetic retrieval marker %s; harmless local demo text. ' "$MARKER"
  for _ in $(seq 1 40); do printf 'Padding sentence for a second chunk. '; done
} > "$DOC_FILE"

UPLOAD="$(curl -sS -X POST "${BASE}/documents/upload" -F "file=@${DOC_FILE};type=text/plain")"
DOC_ID="$(printf '%s' "$UPLOAD" | jq -r '.id // empty')"
[ -n "$DOC_ID" ] || fail "upload did not return a document id: $UPLOAD"
ok "synthetic document uploaded and indexed"

HITS="$(curl -sS -X POST "${BASE}/search" -H 'Content-Type: application/json' \
  -d "{\"query\":\"${MARKER}\",\"limit\":10}")"
echo "$HITS" | jq -e --arg id "$DOC_ID" '[.results[].document_id] | index($id) != null' >/dev/null \
  || fail "document not retrievable before deletion"
ok "retrieval succeeds before deletion"

LINEAGE="$(curl -sS "${BASE}/documents/${DOC_ID}/lineage")"
echo "$LINEAGE" | jq -e '.artifacts | length >= 3' >/dev/null || fail "expected lineage artifacts"
echo "$LINEAGE" | jq -e '.cache_entries | length >= 1' >/dev/null || fail "expected a cache entry after search"
ok "lineage and cache entry recorded"

REPORT="$(curl -sS -X POST "${BASE}/documents/${DOC_ID}/delete" -H "X-Delete-Token: $DELETE_AUTH_TOKEN")"
echo "$REPORT" | jq -e '.status == "pass"' >/dev/null || fail "deletion did not verify: $REPORT"
echo "$REPORT" | jq -e '[.checks[].status] | (length == 6 and all(. == "pass"))' >/dev/null \
  || fail "expected six passing checks"
OP_ID="$(printf '%s' "$REPORT" | jq -r '.operation_id')"
ok "deletion verified: six passing checks"

AFTER="$(curl -sS -X POST "${BASE}/search" -H 'Content-Type: application/json' \
  -d "{\"query\":\"${MARKER}\",\"limit\":10}")"
echo "$AFTER" | jq -e --arg id "$DOC_ID" '[.results[] | select(.document_id == $id)] | length == 0' >/dev/null \
  || fail "deleted document still retrievable"
AGAIN="$(curl -sS -X POST "${BASE}/search" -H 'Content-Type: application/json' \
  -d "{\"query\":\"${MARKER}\",\"limit\":10}")"
echo "$AGAIN" | jq -e --arg id "$DOC_ID" '[.results[] | select(.document_id == $id)] | length == 0' >/dev/null \
  || fail "cache resurrected deleted content"
ok "post-delete retrieval blocked (cache does not resurrect content)"

(cd "$REPO" && PYTHONPATH=. .venv/bin/python -m pytest -q \
  "backend/tests/unit/test_lineage_cache.py::test_stale_cache_acceptance_lifecycle" \
  >/dev/null) || fail "stale-cache residual case failed"
ok "stale-cache residual detected (fail with residual keys), then pass after remediation"

VERIFY="$(curl -sS -X POST "${BASE}/documents/${DOC_ID}/verify")"
echo "$VERIFY" | jq -e '.kind == "verification" and .status == "blocked"' >/dev/null \
  || fail "fresh verification did not re-check backends: $VERIFY"
echo "$VERIFY" | jq -e '[.checks[] | select(.layer == "app_cache") | .status] | all(. == "pass")' >/dev/null \
  || fail "cache check did not pass on fresh verification"
ok "fresh read-only verification: storage re-checked, cache passing"

GUIDED="$(curl -sS -X POST "${BASE}/demo/runs" -H "X-Delete-Token: $DELETE_AUTH_TOKEN")"
GUIDED_DOC_ID="$(printf '%s' "$GUIDED" | jq -r '.document_id // empty')"
GUIDED_OP_ID="$(printf '%s' "$GUIDED" | jq -r '.initial_report.operation_id // empty')"
[ -n "$GUIDED_DOC_ID" ] && [ -n "$GUIDED_OP_ID" ] || fail "guided demo did not return its generated fixture and operation"
echo "$GUIDED" | jq -e '.demo_mode == true and .initial_report.status == "fail"' >/dev/null \
  || fail "guided demo did not preserve the expected residual failure"
echo "$GUIDED" | jq -e '[.initial_report.checks[] | select(.layer == "app_cache" and .status == "fail" and .actual_result.residual_count == 1)] | length == 1' >/dev/null \
  || fail "guided demo report did not record the real cache residual"
echo "$GUIDED" | jq -e '.initial_report.scope.physical_erasure_verified == false and (.initial_report.scope.not_examined | index("unknown_or_unregistered_copies") != null)' >/dev/null \
  || fail "guided demo report did not state evidence limits"
ok "guarded guided demo detects a real persisted cache residual"

GUIDED_REMEDIATION="$(curl -sS -X POST "${BASE}/demo/runs/${GUIDED_OP_ID}/remediate" \
  -H "X-Delete-Token: $DELETE_AUTH_TOKEN")"
GUIDED_FINAL_OP_ID="$(printf '%s' "$GUIDED_REMEDIATION" | jq -r '.final_report.operation_id // empty')"
[ -n "$GUIDED_FINAL_OP_ID" ] || fail "authorized cache remediation did not complete the deletion"
echo "$GUIDED_REMEDIATION" | jq -e '.remediation_report.kind == "remediation" and .remediation_report.status == "pass"' >/dev/null \
  || fail "deterministic cache remediation did not pass"
echo "$GUIDED_REMEDIATION" | jq -e '.final_report.kind == "deletion" and .final_report.status == "pass" and ([.final_report.checks[].status] | all(. == "pass"))' >/dev/null \
  || fail "the final scoped deletion did not pass all configured checks"
echo "$GUIDED_REMEDIATION" | jq -e '.fresh_verification.kind == "verification" and .fresh_verification.status == "blocked" and ([.fresh_verification.checks[] | select(.layer == "source_file" or .layer == "chromadb_api" or .layer == "app_cache" or .layer == "sql_metadata") | .status] | all(. == "pass")) and ([.fresh_verification.checks[] | select(.layer == "application_retrieval") | .status] | all(. == "blocked"))' >/dev/null \
  || fail "fresh verification did not distinguish passed storage checks from the inapplicable retrieval probe"
echo "$GUIDED_REMEDIATION" | jq -e '(.artifact_comparison | length > 0) and ([.artifact_comparison[].state_after] | all(. == "deleted_verified"))' >/dev/null \
  || fail "before/after artifact comparison is incomplete"
ok "authorized remediation, fresh checks, bounded final report, and before/after comparison complete"

kill "$BACKEND_PID" 2>/dev/null
wait "$BACKEND_PID" 2>/dev/null
BACKEND_PID=""
start_backend
wait_for_health
ok "backend restarted against the same isolated store"

curl -sS "${BASE}/deletion-operations/${OP_ID}" | jq -e '.status == "pass"' >/dev/null \
  || fail "report did not persist across restart"
curl -sS "${BASE}/deletion-operations/${GUIDED_OP_ID}" | jq -e '.status == "fail" and .scope.snapshot_version == 1' >/dev/null \
  || fail "initial residual evidence and scope snapshot did not persist across restart"
curl -sS "${BASE}/deletion-operations/${GUIDED_FINAL_OP_ID}" | jq -e '.status == "pass" and .scope.snapshot_version == 1 and (.scope.post_action_checks | length == 6)' >/dev/null \
  || fail "final verification evidence did not persist across restart"
curl -sS "${BASE}/documents/${DOC_ID}/lineage" | jq -e \
  '(.artifacts | length > 0) and ([.artifacts[].status] | all(. == "deleted_verified"))' >/dev/null \
  || fail "lineage did not persist across restart"
curl -sS "${BASE}/documents/${GUIDED_DOC_ID}/lineage" | jq -e \
  '(.artifacts | length > 0) and ([.artifacts[].status] | all(. == "deleted_verified"))' >/dev/null \
  || fail "guided-demo lineage did not persist across restart"
curl -sS "${BASE}/dashboard" | jq -e '.counts.deletion_operations >= 1' >/dev/null \
  || fail "dashboard counts did not persist across restart"
ok "report, lineage, and dashboard persist across restart"

echo "DEMO PASS"
