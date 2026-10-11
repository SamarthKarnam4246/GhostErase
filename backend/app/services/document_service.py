from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.integrations.neatlogs import emit_trace
from backend.app.models.deletion import DeletionCheck, DeletionOperation
from backend.app.models.document import DocumentRecord
from backend.app.integrations.telemetry import telemetry_span
from backend.app.models.lineage import CacheEntry, DerivedArtifact
from backend.app.services.adapters import registered_adapters
from backend.app.services.cache_store import AppCache, count_results, utcnow
from backend.app.services.explainer import (
    ExplanationProvider,
    LiteLLMExplanationProvider,
    build_explanation,
)
from backend.app.services.vector_store import ChromaVectorStore


class DocumentService:
    def __init__(
        self,
        session_factory: Any = SessionLocal,
        vector_store: ChromaVectorStore | None = None,
        upload_dir: str | Path | None = None,
        delete_auth_token: str | None = None,
        cache: AppCache | None = None,
        explainer_provider: ExplanationProvider | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.vector_store = vector_store or ChromaVectorStore()
        self.upload_dir = Path(upload_dir) if upload_dir is not None else settings.upload_dir
        self.delete_auth_token = delete_auth_token if delete_auth_token is not None else settings.delete_auth_token
        self.cache = cache or AppCache(session_factory)
        self.cache_adapter = registered_adapters(self.cache)["app_cache"]
        self.explainer_provider = explainer_provider

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_filename(filename: str) -> str:
        basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", basename).strip("._")
        if not safe or Path(safe).suffix.lower() not in {".txt", ".md"}:
            raise ValueError("Only .txt and .md files are supported.")
        suffix = Path(safe).suffix.lower()
        stem = safe[: -len(suffix)]
        return f"{stem[:180 - len(suffix)]}{suffix}"

    def create_document(self, text: str, source: str = "upload", filename: str | None = None) -> dict[str, Any]:
        with telemetry_span(
            "document.ingestion",
            kind="CHAIN",
            operation_type="document_ingestion",
        ) as span:
            result = self._create_document(text, source, filename)
            span.set_result(
                "pass",
                document_id=result["id"],
                chunk_count=len(result["chunk_ids"]),
            )
            return result

    def _create_document(self, text: str, source: str, filename: str | None) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("Document content cannot be empty.")
        safe_filename = self._safe_filename(filename or "document.txt")
        document_id = str(uuid.uuid4())
        document_dir = self.upload_dir / document_id
        source_path = document_dir / safe_filename
        self.upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        document_dir.mkdir(mode=0o700, exist_ok=False)
        try:
            descriptor = os.open(source_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as source_file:
                source_file.write(text)
            chunk_ids = self.vector_store.add_document(document_id, text, source, safe_filename)
            with self.session_factory() as session:
                record = DocumentRecord(
                    id=document_id,
                    filename=safe_filename,
                    source=source,
                    content=text,
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    chunk_ids=json.dumps(chunk_ids),
                    source_file_path=str(source_path.resolve()),
                    status="indexed",
                    created_at=self._timestamp(),
                )
                session.add(record)
                self._ensure_lineage(
                    session,
                    document_id,
                    safe_filename,
                    str(source_path.resolve()),
                    chunk_ids,
                    self.vector_store.collection.name,
                )
                session.commit()
        except Exception as error:
            cleanup_errors: list[str] = []
            try:
                indexed_ids = self.vector_store.document_chunks(document_id).get("ids", [])
                if indexed_ids:
                    self.vector_store.delete_chunks(document_id, indexed_ids)
            except Exception as cleanup_error:
                cleanup_errors.append(f"vector cleanup: {type(cleanup_error).__name__}")
            try:
                if source_path.exists():
                    source_path.unlink()
                if document_dir.exists():
                    document_dir.rmdir()
            except Exception as cleanup_error:
                cleanup_errors.append(f"source cleanup: {type(cleanup_error).__name__}")
            if cleanup_errors:
                raise RuntimeError(
                    f"Document indexing failed and cleanup was incomplete ({', '.join(cleanup_errors)})."
                ) from error
            raise
        emit_trace(
            "document_indexed",
            {
                "document_id": document_id,
                "operation_type": "document_ingestion",
                "chunk_count": len(chunk_ids),
                "result_status": "pass",
            },
        )
        return {
            "id": document_id,
            "source": source,
            "filename": safe_filename,
            "status": "indexed",
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "chunk_ids": chunk_ids,
        }

    def list_documents(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            records = session.query(DocumentRecord).order_by(DocumentRecord.id).all()
            # Latest pointers track deletion history only; verification runs
            # are separate evidence and must not mask deletion outcomes.
            operations = (
                session.query(DeletionOperation)
                .filter(DeletionOperation.kind == "deletion")
                .order_by(DeletionOperation.created_at.desc(), DeletionOperation.id.desc())
                .all()
            )
            latest_by_document: dict[str, DeletionOperation] = {}
            for operation in operations:
                latest_by_document.setdefault(operation.document_id, operation)
            return [
                {
                    "id": row.id,
                    "filename": row.filename,
                    "source": row.source,
                    "status": row.status,
                    "created_at": row.created_at,
                    "latest_deletion_status": (
                        latest_by_document[row.id].status if row.id in latest_by_document else None
                    ),
                    "latest_deletion_operation_id": (
                        latest_by_document[row.id].id if row.id in latest_by_document else None
                    ),
                    "chunk_count": len(json.loads(row.chunk_ids or "[]")),
                    "content_hash": row.content_hash,
                }
                for row in records
            ]

    def dashboard_data(self) -> dict[str, Any]:
        with self.session_factory() as session:
            documents = session.query(DocumentRecord).all()
            # Dashboard aggregates deletion history only; verification runs
            # are reachable through document actions and direct report reads.
            operations = (
                session.query(DeletionOperation)
                .filter(DeletionOperation.kind == "deletion")
                .order_by(DeletionOperation.created_at.desc(), DeletionOperation.id.desc())
                .all()
            )
            checks = (
                session.query(DeletionCheck.status)
                .join(DeletionOperation, DeletionCheck.operation_id == DeletionOperation.id)
                .filter(DeletionOperation.kind == "deletion")
                .all()
            )
            document_names = {record.id: record.filename for record in documents}

        document_status_counts: dict[str, int] = {}
        for document in documents:
            document_status_counts[document.status or "unknown"] = (
                document_status_counts.get(document.status or "unknown", 0) + 1
            )
        operation_status_counts: dict[str, int] = {}
        for operation in operations:
            operation_status_counts[operation.status] = operation_status_counts.get(operation.status, 0) + 1
        check_status_counts = {"pass": 0, "fail": 0, "blocked": 0, "not_verified": 0}
        for (status,) in checks:
            normalized_status = status if status in check_status_counts else "not_verified"
            check_status_counts[normalized_status] += 1

        return {
            "counts": {
                "indexed_documents": sum(
                    count
                    for status, count in document_status_counts.items()
                    if status != "deleted"
                ),
                "documents_by_status": document_status_counts,
                "deletion_operations": len(operations),
                "deletion_operations_by_status": operation_status_counts,
                "checks": check_status_counts,
            },
            "recent_operations": [
                {
                    "operation_id": operation.id,
                    "document_id": operation.document_id,
                    "document_name": document_names.get(operation.document_id) or "Unknown document",
                    "status": operation.status,
                    "created_at": operation.created_at,
                    "completed_at": operation.completed_at,
                }
                for operation in operations[:50]
            ],
        }

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        with telemetry_span(
            "document.retrieval",
            kind="CHAIN",
            operation_type="application_retrieval",
            top_k=min(max(1, limit), 20),
        ) as span:
            results = self._search(query, limit)
            span.set_result("pass", result_count=len(results))
            return results

    def _search(self, query: str, limit: int) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("Search query cannot be empty.")
        cached = self.cache.get(query)
        if cached:
            # Cache hit: resolve stored IDs against the real collection so a
            # hit never invents records. Falls back to a live query on error.
            try:
                stored = self.vector_store.collection.get(
                    ids=[entry["chunk_id"] for entry in cached],
                    include=["documents", "metadatas"],
                )
                distances = {entry["chunk_id"]: entry["distance"] for entry in cached}
                hits = [
                    {
                        "chunk_id": chunk_id,
                        "document_id": metadata["document_id"],
                        "chunk_index": metadata["chunk_index"],
                        "content": document,
                        "distance": distances.get(chunk_id, 0.0),
                    }
                    for chunk_id, document, metadata in zip(
                        stored.get("ids", []),
                        (stored.get("documents") or []),
                        (stored.get("metadatas") or []),
                    )
                    if metadata and metadata.get("document_id") and document is not None
                ]
            except Exception:
                hits = self.vector_store.query(query, limit)
        else:
            hits = self.vector_store.query(query, limit)
        known_chunks: set[tuple[str, str]] = set()
        with self.session_factory() as session:
            records = (
                session.query(DocumentRecord)
                .filter(DocumentRecord.status != "deleted")
                .all()
            )
            for record in records:
                known_chunks.update((record.id, chunk_id) for chunk_id in json.loads(record.chunk_ids or "[]"))
        filtered = [
            hit
            for hit in hits
            if (hit["document_id"], hit["chunk_id"]) in known_chunks
        ]
        if not cached and filtered:
            # Cache only validated results: unknown or foreign chunk IDs from
            # the shared collection must never become cache rows.
            self.cache.put(query, filtered)
        return filtered

    def _record_check(
        self,
        session: Session,
        operation: DeletionOperation,
        layer: str,
        method: str,
        status: str,
        actual_result: dict[str, Any],
        error_detail: str | None = None,
    ) -> None:
        session.add(
            DeletionCheck(
                id=str(uuid.uuid4()),
                operation_id=operation.id,
                document_id=operation.document_id,
                chunk_ids=operation.chunk_ids,
                layer=layer,
                method=method,
                status=status,
                actual_result=json.dumps(actual_result, sort_keys=True),
                error_detail=error_detail,
                checked_at=self._timestamp(),
            )
        )
        session.commit()
        emit_trace(
            "deletion_check_completed",
            {
                "operation_id": operation.id,
                "document_id": operation.document_id,
                "check_layer": layer,
                "result_status": status,
            },
        )

    def _capture_scope_snapshot(
        self,
        session: Session,
        record: DocumentRecord,
        chunk_ids: list[str],
    ) -> dict[str, Any]:
        artifacts = (
            session.query(DerivedArtifact)
            .filter(DerivedArtifact.document_id == record.id)
            .order_by(DerivedArtifact.id)
            .all()
        )
        cache_entries = (
            session.query(CacheEntry)
            .filter(CacheEntry.document_id == record.id)
            .order_by(CacheEntry.cache_key)
            .all()
        )
        known = {
            artifact.id: {
                "artifact_id": artifact.id,
                "artifact_type": artifact.artifact_type,
                "backend": artifact.backend,
                "state_before": artifact.status,
            }
            for artifact in artifacts
        }
        known.setdefault(
            f"{record.id}:source",
            {
                "artifact_id": f"{record.id}:source",
                "artifact_type": "source_file",
                "backend": "filesystem",
                "state_before": record.status,
            },
        )
        known.setdefault(
            f"{record.id}:sql",
            {
                "artifact_id": f"{record.id}:sql",
                "artifact_type": "sql_content",
                "backend": "sqlite",
                "state_before": "active" if record.content else "cleared",
            },
        )
        for chunk_id in chunk_ids:
            known.setdefault(
                f"{record.id}:chunk:{chunk_id}",
                {
                    "artifact_id": f"{record.id}:chunk:{chunk_id}",
                    "artifact_type": "chroma_chunk",
                    "backend": f"chromadb:{self.vector_store.collection.name}",
                    "state_before": "active",
                },
            )
        for row in cache_entries:
            known.setdefault(
                f"{record.id}:cache:{row.cache_key}",
                {
                    "artifact_id": f"{record.id}:cache:{row.cache_key}",
                    "artifact_type": "app_cache_entry",
                    "backend": "app_cache",
                    "state_before": "active" if row.invalidated_at is None else "invalidated",
                },
            )
        return {
            "version": 1,
            "captured_at": self._timestamp(),
            "declared_scope": {
                "document_id": record.id,
                "artifact_ids": sorted(known),
                "artifact_count": len(known),
            },
            "known_artifacts": [known[key] for key in sorted(known)],
            "configured_systems": [
                "source_filesystem",
                f"chromadb:{self.vector_store.collection.name}",
                "app_cache",
                "sqlite_metadata",
            ],
            "not_examined": [
                "backups",
                "replicas",
                "provider_retention",
                "external_logs",
                "exports",
                "third_party_caches",
                "model_training_effects",
                "unknown_or_unregistered_copies",
            ],
        }

    @staticmethod
    def _result_snapshot(checks: list[DeletionCheck]) -> dict[str, Any]:
        return {
            "captured_at": utcnow(),
            "checks": [
                {
                    "check_id": check.id,
                    "layer": check.layer,
                    "method": check.method,
                    "status": check.status,
                    "checked_at": check.checked_at,
                    "actual_result": json.loads(check.actual_result),
                }
                for check in checks
            ],
        }

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return f"{type(error).__name__}: operation failed; document content and credentials omitted."

    @staticmethod
    def _retrieval_probe_windows(content: str, window_size: int = 1000) -> list[str]:
        head = content[:window_size]
        windows = [head] if head.strip() else []
        if len(content) > window_size:
            middle_start = max(0, (len(content) - window_size) // 2)
            middle = content[middle_start : middle_start + window_size]
            tail = content[-window_size:]
            for candidate in (middle, tail):
                if candidate.strip() and candidate not in windows:
                    windows.append(candidate)
        return windows or [head]

    @staticmethod
    def _ensure_lineage(
        session: Session,
        document_id: str,
        filename: str,
        source_file_path: str | None,
        chunk_ids: list[str],
        collection_name: str,
    ) -> None:
        """Idempotent lineage backfill: add missing artifact rows only.

        Lets documents created before the registry existed gain lineage
        without touching their metadata. Stable IDs make repeats safe.
        """
        timestamp = utcnow()
        source_id = f"{document_id}:source"
        if session.query(DerivedArtifact).filter_by(id=source_id).first() is None:
            session.add(
                DerivedArtifact(
                    id=source_id,
                    document_id=document_id,
                    artifact_type="source_file",
                    backend="filesystem",
                    identifier=source_file_path or filename,
                    parent_id=None,
                    status="active",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        for chunk_id in chunk_ids:
            artifact_id = f"{document_id}:chunk:{chunk_id}"
            if session.query(DerivedArtifact).filter_by(id=artifact_id).first() is None:
                session.add(
                    DerivedArtifact(
                        id=artifact_id,
                        document_id=document_id,
                        artifact_type="chroma_chunk",
                        backend=f"chromadb:{collection_name}",
                        identifier=chunk_id,
                        parent_id=source_id,
                        status="active",
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
        sql_id = f"{document_id}:sql"
        if session.query(DerivedArtifact).filter_by(id=sql_id).first() is None:
            session.add(
                DerivedArtifact(
                    id=sql_id,
                    document_id=document_id,
                    artifact_type="sql_content",
                    backend="sqlite",
                    identifier="documents.content",
                    parent_id=source_id,
                    status="active",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )

    def _mark_artifacts(self, document_id: str, artifact_type: str, status: str) -> None:
        """Best-effort lineage status update in its own transaction.

        The registry is supplementary: check rows are the source of truth, so
        a registry write failure must never break deletion orchestration.
        """
        try:
            with self.session_factory() as session:
                session.query(DerivedArtifact).filter(
                    DerivedArtifact.document_id == document_id,
                    DerivedArtifact.artifact_type == artifact_type,
                ).update({"status": status, "updated_at": utcnow()})
                session.commit()
        except Exception:
            pass

    @staticmethod
    def _derive_overall(statuses: set[str], required_total: int, recorded_total: int) -> str:
        """Single overall-status rule shared by deletion and verification.

        pass only when every required check ran and passed; fail when anything
        failed; otherwise blocked. A check that cannot run is never a pass.
        """
        if recorded_total == required_total and statuses == {"pass"}:
            return "pass"
        if "fail" in statuses:
            return "fail"
        return "blocked"

    def _resolve_source_target(
        self, document_id: str, safe_filename: str, source_file_path: str | None
    ) -> tuple[Path, str]:
        """Validate the document-scoped source path. Returns (target, fingerprint).

        Raises ValueError on symlinks or path mismatches. Never touches the
        filesystem beyond status checks; callers decide removal vs observation.
        """
        if not source_file_path:
            raise ValueError("No source path is recorded for this document.")
        root = self.upload_dir.resolve()
        document_directory = root / document_id
        expected_path = document_directory / safe_filename
        target = Path(source_file_path).absolute()
        if (
            document_directory.is_symlink()
            or expected_path.is_symlink()
            or target != expected_path
        ):
            raise ValueError("Source path does not match the document-scoped upload path.")
        return target, hashlib.sha256(str(target).encode("utf-8")).hexdigest()

    def delete_document(
        self,
        document_id: str,
        authorization_token: str | None,
        *,
        preserve_demo_cache: bool = False,
    ) -> dict[str, Any]:
        with telemetry_span(
            "deletion.orchestration",
            kind="CHAIN",
            document_id=document_id,
            operation_type="deletion",
        ) as span:
            result = self._delete_document(
                document_id,
                authorization_token,
                preserve_demo_cache=preserve_demo_cache,
            )
            span.set_result(
                result.get("status", "not_verified"),
                operation_id=result.get("operation_id"),
            )
            return result

    def start_guided_demo(self, authorization_token: str | None) -> dict[str, Any]:
        if not settings.demo_mode:
            raise PermissionError("Guided demo mode is disabled.")
        if not self._authorization_matches(authorization_token):
            raise PermissionError("A configured deletion authorization token is required.")

        marker = f"ghost-erase-demo-{uuid.uuid4().hex}"
        query = f"synthetic canary {marker}"
        content = (
            f"GhostErase synthetic demo fixture. The synthetic canary is {marker}. "
            "This generated record exists only to demonstrate scoped retrieval and deletion. "
            + "Additional harmless fixture text for local retrieval. " * 40
        )
        created = self.create_document(content, source="guided_demo", filename="guided-demo.txt")
        document_id = created["id"]
        hits = self.search(query, limit=10)
        if not any(hit["document_id"] == document_id for hit in hits):
            raise RuntimeError(
                f"Guided fixture {document_id} was indexed but the configured retrieval path did not return it."
            )
        before = self.document_detail(document_id)
        report = self.delete_document(
            document_id,
            authorization_token,
            preserve_demo_cache=True,
        )
        cache_check = next(
            (check for check in report.get("checks", []) if check["layer"] == "app_cache"),
            None,
        )
        if (
            report.get("status") != "fail"
            or cache_check is None
            or cache_check["status"] != "fail"
            or cache_check["actual_result"].get("residual_count", 0) < 1
        ):
            raise RuntimeError(
                f"Guided fixture {document_id} did not produce the expected persisted cache residual evidence."
            )
        return {
            "demo_mode": True,
            "document_id": document_id,
            "query": query,
            "initial_report": report,
            "before": before,
            "next_step": "Investigate persisted evidence, then authorize cache remediation.",
        }

    def remediate_guided_demo(
        self,
        operation_id: str,
        authorization_token: str | None,
    ) -> dict[str, Any]:
        if not settings.demo_mode:
            raise PermissionError("Guided demo mode is disabled.")
        if not self._authorization_matches(authorization_token):
            raise PermissionError("A configured deletion authorization token is required.")

        with self.session_factory() as session:
            failed_operation = (
                session.query(DeletionOperation)
                .filter_by(id=operation_id, kind="deletion", status="fail")
                .first()
            )
            if failed_operation is None:
                raise ValueError("A failed guided-demo deletion operation is required.")
            record = (
                session.query(DocumentRecord)
                .filter_by(id=failed_operation.document_id, source="guided_demo")
                .first()
            )
            if record is None:
                raise ValueError("Only generated guided-demo fixtures can be remediated here.")
            failed_cache_check = (
                session.query(DeletionCheck)
                .filter_by(operation_id=operation_id, layer="app_cache", status="fail")
                .first()
            )
            if failed_cache_check is None:
                raise ValueError("The guided-demo report has no failed app-cache evidence.")
            failed_cache_result = json.loads(failed_cache_check.actual_result)
            if failed_cache_result.get("residual_count", 0) < 1:
                raise ValueError("The guided-demo report does not contain a cache residual.")
            document_id = record.id
            chunk_ids = json.loads(failed_operation.chunk_ids or "[]")
            scope_snapshot = self._capture_scope_snapshot(session, record, chunk_ids)
            remediation = DeletionOperation(
                id=str(uuid.uuid4()),
                document_id=document_id,
                chunk_ids=json.dumps(chunk_ids),
                status="running",
                kind="remediation",
                created_at=self._timestamp(),
                scope_snapshot=json.dumps(scope_snapshot, sort_keys=True),
            )
            session.add(remediation)
            session.commit()
            remediation_id = remediation.id

        with self.session_factory() as session:
            remediation = session.query(DeletionOperation).filter_by(id=remediation_id).one()
            try:
                removal = self.cache_adapter.remove(document_id)
                residuals = self.cache_adapter.residuals(document_id)
                residual_keys = sorted(residuals.get("residual_cache_keys", []))
                status = "pass" if not residual_keys else "fail"
                result = {
                    **removal,
                    "residual_cache_keys": residual_keys,
                    "residual_count": len(residual_keys),
                    "authorization": "delete_token_validated",
                    "target_scope": "generated_guided_demo_document",
                }
                if status == "pass":
                    self._mark_artifacts(document_id, "app_cache_entry", "deleted_verified")
                else:
                    self._mark_artifacts(document_id, "app_cache_entry", "residual_detected")
                error_detail = None
            except Exception as error:
                status = "blocked"
                result = {
                    "authorization": "delete_token_validated",
                    "target_scope": "generated_guided_demo_document",
                }
                error_detail = self._safe_error(error)
            self._record_check(
                session,
                remediation,
                "app_cache",
                "authorized cache.invalidate(document_id) then cache.residuals(document_id)",
                status,
                result,
                error_detail,
            )
            remediation_checks = (
                session.query(DeletionCheck)
                .filter_by(operation_id=remediation_id)
                .all()
            )
            remediation.result_snapshot = json.dumps(
                self._result_snapshot(remediation_checks),
                sort_keys=True,
            )
            remediation.status = self._derive_overall(
                {check.status for check in remediation_checks},
                1,
                len(remediation_checks),
            )
            remediation.completed_at = self._timestamp()
            session.commit()

        remediation_report = self.deletion_report(remediation_id)
        if remediation_report is None or remediation_report["status"] != "pass":
            return {
                "document_id": document_id,
                "remediation_report": remediation_report,
                "final_report": None,
                "fresh_verification": None,
                "before": self.document_detail(document_id),
                "after": self.document_detail(document_id),
            }

        pre_completion_verification = self.verify_document(document_id)
        final_report = self.delete_document(document_id, authorization_token)
        fresh_verification = (
            self.verify_document(document_id)
            if final_report.get("status") == "pass"
            else None
        )
        before = self.document_detail(document_id, operation_id=operation_id)
        after = self.document_detail(
            document_id,
            operation_id=final_report.get("operation_id"),
        )
        before_states = {
            artifact["artifact_id"]: artifact["state_before"]
            for artifact in (before or {}).get("scope_snapshot", {}).get("known_artifacts", [])
        }
        after_states = {
            artifact["artifact_id"]: artifact["status"]
            for artifact in (after or {}).get("artifacts", [])
        }
        artifact_comparison = [
            {
                "artifact_id": artifact_id,
                "state_before": before_states.get(artifact_id, "not_in_initial_snapshot"),
                "state_after": after_states.get(artifact_id, "not_in_final_registry"),
            }
            for artifact_id in sorted(set(before_states) | set(after_states))
        ]
        return {
            "document_id": document_id,
            "remediation_report": remediation_report,
            "pre_completion_verification": pre_completion_verification,
            "final_report": final_report,
            "fresh_verification": fresh_verification,
            "before": before,
            "after": after,
            "artifact_comparison": artifact_comparison,
        }

    def _authorization_matches(self, authorization_token: str | None) -> bool:
        return bool(
            self.delete_auth_token
            and authorization_token
            and secrets.compare_digest(
                self.delete_auth_token.encode("utf-8"),
                authorization_token.encode("utf-8"),
            )
        )

    def _delete_document(
        self,
        document_id: str,
        authorization_token: str | None,
        *,
        preserve_demo_cache: bool = False,
    ) -> dict[str, Any]:
        if not self.delete_auth_token or not authorization_token or not secrets.compare_digest(
            self.delete_auth_token.encode("utf-8"),
            authorization_token.encode("utf-8"),
        ):
            return {
                "document_id": document_id,
                "status": "blocked",
                "reason": "A configured deletion authorization token is required.",
            }

        try:
            with self.session_factory() as session:
                record = session.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
                if record is None:
                    return {"document_id": document_id, "status": "not_verified", "reason": "Document not found."}
                if preserve_demo_cache and (not settings.demo_mode or record.source != "guided_demo"):
                    return {
                        "document_id": document_id,
                        "status": "blocked",
                        "reason": "Demo cache fault injection is restricted to generated guided-demo records.",
                    }
                if record.status == "deleted":
                    completed_operation = (
                        session.query(DeletionOperation)
                        .filter(
                            DeletionOperation.document_id == document_id,
                            DeletionOperation.status == "pass",
                            DeletionOperation.kind == "deletion",
                        )
                        .order_by(DeletionOperation.created_at.desc())
                        .first()
                    )
                    if completed_operation is not None:
                        historical = self.deletion_report(completed_operation.id)
                        if historical is not None:
                            # No new backend check ran for this response; say so
                            # explicitly rather than implying fresh verification.
                            historical["served_from_history"] = True
                            return historical
                        return {
                            "document_id": document_id,
                            "status": "not_verified",
                            "reason": "Completed deletion report was not found.",
                        }
                chunk_ids = json.loads(record.chunk_ids or "[]")
                original_content = record.content
                operation = DeletionOperation(
                    id=str(uuid.uuid4()),
                    document_id=document_id,
                    chunk_ids=json.dumps(chunk_ids),
                    status="running",
                    created_at=self._timestamp(),
                    scope_snapshot=json.dumps(
                        self._capture_scope_snapshot(session, record, chunk_ids),
                        sort_keys=True,
                    ),
                )
                session.add(operation)
                session.commit()
                operation_id = operation.id
                source_file_path = record.source_file_path
                safe_filename = record.filename
                # Legacy documents created before the registry gain lineage here.
                self._ensure_lineage(
                    session,
                    document_id,
                    safe_filename,
                    source_file_path,
                    chunk_ids,
                    self.vector_store.collection.name,
                )
                try:
                    session.commit()
                except IntegrityError:
                    # A concurrent request backfilled the same stable artifact
                    # IDs first. Roll back the lineage adds only (the operation
                    # row committed above) and continue with those rows as-is.
                    session.rollback()
        except SQLAlchemyError:
            # The metadata store could not be read or written (locked,
            # unavailable). Record nothing: claiming anything else would be
            # fabricated evidence.
            return {
                "document_id": document_id,
                "status": "not_verified",
                "reason": "Document metadata is currently unavailable; no operation was recorded.",
            }

        with self.session_factory() as session:
            operation = session.query(DeletionOperation).filter(DeletionOperation.id == operation_id).one()
            source_status = "blocked"
            source_result: dict[str, Any] = {"path_configured": bool(source_file_path)}
            source_error: str | None = None
            if source_file_path:
                try:
                    target, source_path_fingerprint = self._resolve_source_target(
                        document_id, safe_filename, source_file_path
                    )
                    source_result["source_path_fingerprint"] = source_path_fingerprint
                    if not target.is_file():
                        prior_removal = (
                            session.query(DeletionCheck)
                            .filter(
                                DeletionCheck.document_id == document_id,
                                DeletionCheck.layer == "source_file",
                                DeletionCheck.status == "pass",
                                DeletionCheck.operation_id != operation_id,
                            )
                            .order_by(DeletionCheck.checked_at.desc())
                            .first()
                        )
                        prior_result = json.loads(prior_removal.actual_result) if prior_removal else {}
                        # Either a prior deletion (file_exists_after_delete) or a
                        # prior fresh verification (file_exists) may attest the
                        # absence, as long as the path fingerprint matches.
                        if (
                            prior_result.get("source_path_fingerprint") == source_path_fingerprint
                            and (
                                prior_result.get("file_exists_after_delete") is False
                                or prior_result.get("file_exists") is False
                            )
                        ):
                            source_status = "pass"
                            source_result.update(
                                {
                                    "file_exists_before_delete": False,
                                    "removal_previously_completed": True,
                                    "evidence_operation_id": prior_removal.operation_id,
                                    "source_path_fingerprint": source_path_fingerprint,
                                }
                            )
                        else:
                            source_status = "fail"
                            source_result["file_existed_before_delete"] = False
                    else:
                        with telemetry_span(
                            "deletion.source_removal",
                            kind="TOOL",
                            operation_id=operation_id,
                            document_id=document_id,
                            operation_type="source_file_delete",
                        ) as span:
                            target.unlink()
                            source_result["file_existed_before_delete"] = True
                            source_result["file_exists_after_delete"] = target.exists()
                            source_result["source_path_fingerprint"] = source_path_fingerprint
                            source_status = "pass" if not target.exists() else "fail"
                            span.set_result(source_status)
                except Exception as error:
                    source_status = "fail"
                    source_error = self._safe_error(error)
            # Claim: the configured source path is absent after this operation.
            # Limitation: ordinary unlink only; journals/SSD/backups unexamined.
            self._record_check(
                session,
                operation,
                "source_file",
                "path.unlink_and_path.exists",
                source_status,
                source_result,
                source_error,
            )
            if source_status == "pass":
                self._mark_artifacts(document_id, "source_file", "deleted_verified")

            delete_status = "blocked"
            delete_result: dict[str, Any] = {"selected_chunk_ids": chunk_ids}
            delete_error: str | None = None
            try:
                delete_result.update(self.vector_store.delete_chunks(document_id, chunk_ids))
                delete_status = "pass"
            except Exception as error:
                delete_status = "fail"
                delete_error = self._safe_error(error)
            # Acknowledgement only (api_response_received); the residual check
            # below is what verifies removal. Never sufficient alone for pass.
            self._record_check(
                session,
                operation,
                "chromadb_api",
                "collection.get(ids, where=document_id) then collection.delete(ids, where=document_id)",
                delete_status,
                delete_result,
                delete_error,
            )

            residual_status = "blocked"
            residual_result: dict[str, Any] = {}
            residual_error: str | None = None
            try:
                residuals = self.vector_store.document_chunks(document_id)
                residual_ids = sorted(residuals.get("ids", []))
                residual_status = "fail" if residual_ids else "pass"
                residual_result = {"residual_chunk_ids": residual_ids, "residual_count": len(residual_ids)}
            except Exception as error:
                residual_status = "blocked"
                residual_error = self._safe_error(error)
            # Claim: no tracked chunk rows remain for this document in the
            # configured collection. Limitation: this collection/API only.
            self._record_check(
                session,
                operation,
                "chromadb_api",
                "collection.get(where=document_id)",
                residual_status,
                residual_result,
                residual_error,
            )
            if delete_status == "pass" and residual_status == "pass":
                self._mark_artifacts(document_id, "chroma_chunk", "deleted_verified")
            elif residual_status == "fail":
                self._mark_artifacts(document_id, "chroma_chunk", "residual_detected")

            # Claim: no active app-cache rows remain linked to this document.
            # Limitation: this application-owned demo cache only; arbitrary
            # third-party caches cannot be inspected and stay unverified.
            cache_status = "blocked"
            cache_result: dict[str, Any] = {}
            cache_error: str | None = None
            if preserve_demo_cache:
                cache_result["demo_fault_injection"] = "cache_invalidation_preserved"
            else:
                try:
                    cache_result.update(self.cache_adapter.remove(document_id))
                except Exception as error:
                    cache_status = "fail"
                    cache_error = self._safe_error(error)
            try:
                cache_residuals = self.cache_adapter.residuals(document_id)
                residual_keys = sorted(cache_residuals.get("residual_cache_keys", []))
                if residual_keys:
                    cache_status = "fail"
                    self._mark_artifacts(document_id, "app_cache_entry", "residual_detected")
                elif cache_status != "fail":
                    cache_status = "pass"
                cache_result.update(
                    {"residual_cache_keys": residual_keys, "residual_count": len(residual_keys)}
                )
            except Exception as error:
                if cache_status != "fail":
                    cache_status = "blocked"
                    cache_error = self._safe_error(error)
            if cache_status == "pass":
                self._mark_artifacts(document_id, "app_cache_entry", "deleted_verified")
            self._record_check(
                session,
                operation,
                "app_cache",
                (
                    "demo fault injection preserves cache then cache.residuals(document_id)"
                    if preserve_demo_cache
                    else "cache.invalidate(document_id) then cache.residuals(document_id)"
                ),
                cache_status,
                cache_result,
                cache_error,
            )

            retrieval_status = "blocked"
            retrieval_result: dict[str, Any] = {}
            retrieval_error: str | None = None
            if original_content:
                try:
                    # Claim: the normal app retrieval path returns no chunks for
                    # this document across head/middle/tail probes. Limitation:
                    # evidence only for the probed queries, not for every
                    # possible query string.
                    windows = self._retrieval_probe_windows(original_content)
                    matching_ids: set[str] = set()
                    returned_document_ids: set[str] = set()
                    result_count = 0
                    for window in windows:
                        hits = self.vector_store.query(window, limit=10)
                        matching_ids.update(
                            hit["chunk_id"] for hit in hits if hit["document_id"] == document_id
                        )
                        returned_document_ids.update(hit["document_id"] for hit in hits)
                        result_count += len(hits)
                    retrieval_status = "fail" if matching_ids else "pass"
                    retrieval_result = {
                        "matching_chunk_ids": sorted(matching_ids),
                        "returned_document_ids": sorted(returned_document_ids),
                        "result_count": result_count,
                        "probed_window_count": len(windows),
                    }
                except Exception as error:
                    retrieval_error = self._safe_error(error)
            else:
                retrieval_error = "Original document content is unavailable for a post-deletion retrieval query."
            self._record_check(
                session,
                operation,
                "application_retrieval",
                "normal Chroma-backed query path using an in-memory pre-deletion query",
                retrieval_status,
                retrieval_result,
                retrieval_error,
            )

            record = session.query(DocumentRecord).filter(DocumentRecord.id == document_id).one()
            prior_checks = (
                session.query(DeletionCheck)
                .filter(DeletionCheck.operation_id == operation_id)
                .all()
            )
            prior_statuses = {check.status for check in prior_checks}
            if len(prior_checks) == 5 and prior_statuses == {"pass"}:
                try:
                    with telemetry_span(
                        "deletion.metadata_clear",
                        kind="TOOL",
                        operation_id=operation_id,
                        document_id=document_id,
                        operation_type="sql_content_clear",
                    ) as span:
                        record.content = ""
                        record.deleted_at = self._timestamp()
                        session.commit()
                        persisted_content = (
                            session.query(DocumentRecord.content)
                            .filter(DocumentRecord.id == document_id)
                            .scalar()
                        )
                        metadata_status = "pass" if not persisted_content else "fail"
                        metadata_result = {"content_removed": not persisted_content, "document_id": document_id}
                        metadata_error = None
                        span.set_result(metadata_status)
                except Exception as error:
                    # Claim: SQL content was NOT cleared and is retained for
                    # recovery. Limitation: the write path itself is not
                    # verified beyond this failed attempt. Roll back so the
                    # recorded evidence checks and finalization still persist.
                    session.rollback()
                    metadata_status = "blocked"
                    metadata_result = {"content_removed": False, "document_id": document_id}
                    metadata_error = (
                        "SQLite content was retained because the metadata-clear "
                        "write did not complete. " + self._safe_error(error)
                    )
            else:
                metadata_status = "blocked"
                metadata_result = {"content_removed": False, "document_id": document_id}
                metadata_error = "SQLite content was retained because a required prior deletion check did not pass."
            self._record_check(
                session,
                operation,
                "sql_metadata",
                "SQLAlchemy clear content only after prior checks pass, then re-read document record",
                metadata_status,
                metadata_result,
                metadata_error,
            )
            if metadata_status == "pass":
                self._mark_artifacts(document_id, "sql_content", "deleted_verified")

            checks = (
                session.query(DeletionCheck)
                .filter(DeletionCheck.operation_id == operation_id)
                .all()
            )
            operation.result_snapshot = json.dumps(
                self._result_snapshot(checks),
                sort_keys=True,
            )
            required_statuses = {check.status for check in checks}
            operation.status = self._derive_overall(required_statuses, 6, len(checks))
            if operation.status == "pass":
                record.status = "deleted"
            elif operation.status == "fail":
                # A concurrent winner may already have terminally deleted this
                # document. Its state must win atomically: a slower loser must
                # not overwrite cleared content with a failure status, while
                # the losing operation still records its own honest result.
                session.execute(
                    update(DocumentRecord)
                    .where(
                        DocumentRecord.id == document_id,
                        DocumentRecord.status != "deleted",
                    )
                    .values(status="deletion_failed")
                )
            else:
                operation.status = "blocked"
                session.execute(
                    update(DocumentRecord)
                    .where(
                        DocumentRecord.id == document_id,
                        DocumentRecord.status != "deleted",
                    )
                    .values(status="deletion_blocked")
                )
            operation.completed_at = self._timestamp()
            final_status = operation.status
            session.commit()

        emit_trace(
            "deletion_operation_completed",
            {
                "operation_id": operation_id,
                "document_id": document_id,
                "operation_type": "deletion",
                "result_status": final_status,
            },
        )
        return self.deletion_report(operation_id)

    def deletion_report(self, operation_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            operation = (
                session.query(DeletionOperation)
                .filter(DeletionOperation.id == operation_id)
                .first()
            )
            if operation is None:
                return None
            checks = (
                session.query(DeletionCheck)
                .filter(DeletionCheck.operation_id == operation_id)
                .order_by(DeletionCheck.checked_at, DeletionCheck.id)
                .all()
            )
            scope_snapshot = (
                json.loads(operation.scope_snapshot)
                if operation.scope_snapshot
                else {}
            )
            result_snapshot = (
                json.loads(operation.result_snapshot)
                if operation.result_snapshot
                else {}
            )
            return {
                "operation_id": operation.id,
                "document_id": operation.document_id,
                "chunk_ids": json.loads(operation.chunk_ids),
                "status": operation.status,
                "kind": operation.kind,
                # False here: this payload was assembled for a freshly executed
                # or directly requested operation. The re-delete shortcut below
                # overrides it when serving historical evidence instead.
                "served_from_history": False,
                "created_at": operation.created_at,
                "completed_at": operation.completed_at,
                "checks": [
                    {
                        "check_id": check.id,
                        "operation_id": check.operation_id,
                        "checked_at": check.checked_at,
                        "document_id": check.document_id,
                        "chunk_ids": json.loads(check.chunk_ids),
                        "layer": check.layer,
                        "method": check.method,
                        "status": check.status,
                        "actual_result": json.loads(check.actual_result),
                        "error_detail": check.error_detail,
                    }
                    for check in checks
                ],
                "scope": {
                    "api_level_accessibility_only": True,
                    "snapshot_version": 1,
                    "scope_captured_at": scope_snapshot.get("captured_at"),
                    "declared_scope": scope_snapshot.get(
                        "declared_scope",
                        {"document_id": operation.document_id, "artifact_ids": []},
                    ),
                    "pre_action_artifacts": scope_snapshot.get("known_artifacts", []),
                    "post_action_checks": result_snapshot.get("checks", []),
                    "checked_layers": [
                        {"layer": check.layer, "method": check.method, "status": check.status}
                        for check in checks
                    ],
                    "storage_paths_checked": {
                        "source_file": "source_file_path beneath configured GHOSTERASE_UPLOAD_DIR",
                        "chromadb": {
                            "path": str(self.vector_store.path),
                            "collection": self.vector_store.collection.name,
                        },
                        "sql_metadata": "configured DATABASE_URL documents.content field",
                    },
                    "regression_test_scope": {
                        "executed_as_part_of_this_operation": False,
                        "repository_cases": [
                            "unique synthetic marker is retrievable before deletion and absent after",
                            "unrelated control document remains retrievable",
                            "failed ChromaDB delete remains failed and preserves residual evidence",
                            "failed operation can be retried using persisted source-removal evidence",
                            "missing authorization is rejected before operation creation",
                            "chunk selection cannot include another document",
                        ],
                    },
                    "physical_erasure_verified": False,
                    "conclusion": (
                        "All configured API-level checks passed."
                        if operation.status == "pass"
                        else "At least one required check failed, was blocked, or remains unverified."
                    ),
                    "not_examined": sorted(
                        {
                            "SSD remanence",
                            "filesystem journals",
                            "backups",
                            "storage outside configured application layers",
                            *scope_snapshot.get("not_examined", []),
                        }
                    ),
                },
            }

    def verify_document(self, document_id: str) -> dict[str, Any]:
        with telemetry_span(
            "deletion.verification_check",
            kind="CHAIN",
            document_id=document_id,
            operation_type="deletion",
        ) as span:
            result = self._verify_current_state(document_id)
            span.set_result(
                result.get("status", "not_verified"),
                operation_id=result.get("operation_id"),
            )
            return result

    def _verify_current_state(self, document_id: str) -> dict[str, Any]:
        """Re-query the real supported backends without changing anything.

        Read-only: no unlink, no vector/cache/SQL writes, no artifact status
        changes. Persists a new kind='verification' operation with one check
        per layer plus any errors. Overall status uses the same gate as
        deletion: pass only when every check ran and passed; fail when
        anything was found present; otherwise blocked. A probe that cannot run
        (e.g., no retained content) is blocked, never passed.
        """
        try:
            with self.session_factory() as session:
                record = session.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
                if record is None:
                    return {"document_id": document_id, "status": "not_verified", "reason": "Document not found."}
                chunk_ids = json.loads(record.chunk_ids or "[]")
                original_content = record.content
                operation = DeletionOperation(
                    id=str(uuid.uuid4()),
                    document_id=document_id,
                    chunk_ids=json.dumps(chunk_ids),
                    status="running",
                    kind="verification",
                    created_at=self._timestamp(),
                    scope_snapshot=json.dumps(
                        self._capture_scope_snapshot(session, record, chunk_ids),
                        sort_keys=True,
                    ),
                )
                session.add(operation)
                session.commit()
                operation_id = operation.id
                source_file_path = record.source_file_path
                safe_filename = record.filename
        except SQLAlchemyError:
            return {
                "document_id": document_id,
                "status": "not_verified",
                "reason": "Document metadata is currently unavailable; no operation was recorded.",
            }

        with self.session_factory() as session:
            operation = session.query(DeletionOperation).filter(DeletionOperation.id == operation_id).one()

            source_status = "blocked"
            source_result: dict[str, Any] = {"path_configured": bool(source_file_path)}
            source_error: str | None = None
            if source_file_path:
                try:
                    target, source_path_fingerprint = self._resolve_source_target(
                        document_id, safe_filename, source_file_path
                    )
                    exists = target.is_file()
                    source_status = "fail" if exists else "pass"
                    source_result.update(
                        {
                            "file_exists": exists,
                            "source_path_fingerprint": source_path_fingerprint,
                        }
                    )
                except Exception as error:
                    source_status = "fail"
                    source_error = self._safe_error(error)
            self._record_check(
                session,
                operation,
                "source_file",
                "path.exists",
                source_status,
                source_result,
                source_error,
            )

            residual_status = "blocked"
            residual_result: dict[str, Any] = {}
            residual_error: str | None = None
            try:
                residuals = self.vector_store.document_chunks(document_id)
                residual_ids = sorted(residuals.get("ids", []))
                residual_status = "fail" if residual_ids else "pass"
                residual_result = {"residual_chunk_ids": residual_ids, "residual_count": len(residual_ids)}
            except Exception as error:
                residual_status = "blocked"
                residual_error = self._safe_error(error)
            self._record_check(
                session,
                operation,
                "chromadb_api",
                "collection.get(where=document_id)",
                residual_status,
                residual_result,
                residual_error,
            )

            cache_status = "blocked"
            cache_result: dict[str, Any] = {}
            cache_error: str | None = None
            try:
                cache_residuals = self.cache_adapter.residuals(document_id)
                residual_keys = sorted(cache_residuals.get("residual_cache_keys", []))
                cache_status = "fail" if residual_keys else "pass"
                cache_result = {
                    "residual_cache_keys": residual_keys,
                    "residual_count": len(residual_keys),
                }
            except Exception as error:
                cache_status = "blocked"
                cache_error = self._safe_error(error)
            self._record_check(
                session,
                operation,
                "app_cache",
                "cache.residuals(document_id)",
                cache_status,
                cache_result,
                cache_error,
            )

            retrieval_status = "blocked"
            retrieval_result: dict[str, Any] = {}
            retrieval_error: str | None = None
            if original_content:
                try:
                    windows = self._retrieval_probe_windows(original_content)
                    matching_ids: set[str] = set()
                    returned_document_ids: set[str] = set()
                    result_count = 0
                    for window in windows:
                        hits = self.vector_store.query(window, limit=10)
                        matching_ids.update(
                            hit["chunk_id"] for hit in hits if hit["document_id"] == document_id
                        )
                        returned_document_ids.update(hit["document_id"] for hit in hits)
                        result_count += len(hits)
                    retrieval_status = "fail" if matching_ids else "pass"
                    retrieval_result = {
                        "matching_chunk_ids": sorted(matching_ids),
                        "returned_document_ids": sorted(returned_document_ids),
                        "result_count": result_count,
                        "probed_window_count": len(windows),
                    }
                except Exception as error:
                    retrieval_error = self._safe_error(error)
            else:
                retrieval_error = (
                    "Original document content is unavailable for a retrieval probe;"
                    " storage-state checks govern this verification."
                )
            self._record_check(
                session,
                operation,
                "application_retrieval",
                "normal Chroma-backed query path using retained content",
                retrieval_status,
                retrieval_result,
                retrieval_error,
            )

            sql_status = "blocked"
            sql_result: dict[str, Any] = {}
            sql_error: str | None = None
            try:
                persisted_content = (
                    session.query(DocumentRecord.content)
                    .filter(DocumentRecord.id == document_id)
                    .scalar()
                )
                content_present = bool(persisted_content)
                sql_status = "fail" if content_present else "pass"
                sql_result = {"content_present": content_present, "document_id": document_id}
            except Exception as error:
                sql_error = self._safe_error(error)
            self._record_check(
                session,
                operation,
                "sql_metadata",
                "SQLAlchemy read document record; no writes",
                sql_status,
                sql_result,
                sql_error,
            )

            checks = (
                session.query(DeletionCheck)
                .filter(DeletionCheck.operation_id == operation_id)
                .all()
            )
            operation.result_snapshot = json.dumps(
                self._result_snapshot(checks),
                sort_keys=True,
            )
            operation.status = self._derive_overall(
                {check.status for check in checks}, 5, len(checks)
            )
            operation.completed_at = self._timestamp()
            final_status = operation.status
            session.commit()

        emit_trace(
            "verification_operation_completed",
            {
                "operation_id": operation_id,
                "document_id": document_id,
                "operation_type": "verification",
                "result_status": final_status,
            },
        )
        return self.deletion_report(operation_id)

    def explain_operation(self, operation_id: str) -> dict[str, Any] | None:
        """Advisory AI explanation of a persisted report.

        Returns None for unknown operations. Never mutates operations, checks,
        or the verified status -- the payload echoes the stored status.
        """
        report = self.deletion_report(operation_id)
        if report is None:
            return None
        provider = self.explainer_provider
        if provider is None and (settings.litellm_api_key or settings.groq_api_key):
            provider = LiteLLMExplanationProvider()
        return build_explanation(report, provider)

    def document_detail(
        self,
        document_id: str,
        operation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Read-only document summary plus known lineage. No raw content."""
        with self.session_factory() as session:
            record = session.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
            if record is None:
                return None
            latest_operation = (
                session.query(DeletionOperation)
                .filter(DeletionOperation.document_id == document_id)
                .order_by(DeletionOperation.created_at.desc(), DeletionOperation.id.desc())
                .first()
            )
            artifacts = (
                session.query(DerivedArtifact)
                .filter(DerivedArtifact.document_id == document_id)
                .order_by(DerivedArtifact.id)
                .all()
            )
            cache_rows = (
                session.query(CacheEntry)
                .filter(CacheEntry.document_id == document_id)
                .order_by(CacheEntry.cache_key)
                .all()
            )
            operation_snapshot: dict[str, Any] | None = None
            if operation_id:
                operation = (
                    session.query(DeletionOperation)
                    .filter_by(id=operation_id, document_id=document_id)
                    .first()
                )
                if operation is not None and operation.scope_snapshot:
                    operation_snapshot = json.loads(operation.scope_snapshot)
            detail = {
                "document": {
                    "id": record.id,
                    "filename": record.filename,
                    "source": record.source,
                    "status": record.status,
                    "created_at": record.created_at,
                    "latest_deletion_status": latest_operation.status if latest_operation else None,
                    "latest_deletion_operation_id": latest_operation.id if latest_operation else None,
                    "chunk_count": len(json.loads(record.chunk_ids or "[]")),
                    "content_hash": record.content_hash,
                },
                "artifacts": [
                    {
                        "artifact_id": row.id,
                        "artifact_type": row.artifact_type,
                        "backend": row.backend,
                        "identifier": row.identifier,
                        "parent_id": row.parent_id,
                        "status": row.status,
                        "created_at": row.created_at,
                        "updated_at": row.updated_at,
                    }
                    for row in artifacts
                ],
                "cache_entries": [
                    {
                        "cache_key": row.cache_key,
                        "query_hash": row.query_hash,
                        "result_count": count_results(row.result_ids),
                        "created_at": row.created_at,
                        "invalidated_at": row.invalidated_at,
                    }
                    for row in cache_rows
                ],
                "lineage_note": (
                    "Known application-tracked artifacts only. External copies "
                    "(backups, replicas, provider retention, conversation memory, "
                    "logs, exports) are not tracked and remain unverified."
                ),
            }
            if operation_snapshot is not None:
                detail["scope_snapshot"] = operation_snapshot
            return detail
