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

from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.integrations.neatlogs import emit_trace
from backend.app.models.deletion import DeletionCheck, DeletionOperation
from backend.app.models.document import DocumentRecord
from backend.app.integrations.telemetry import telemetry_span
from backend.app.services.vector_store import ChromaVectorStore


class DocumentService:
    def __init__(
        self,
        session_factory: Any = SessionLocal,
        vector_store: ChromaVectorStore | None = None,
        upload_dir: str | Path | None = None,
        delete_auth_token: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.vector_store = vector_store or ChromaVectorStore()
        self.upload_dir = Path(upload_dir) if upload_dir is not None else settings.upload_dir
        self.delete_auth_token = delete_auth_token if delete_auth_token is not None else settings.delete_auth_token

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
            operations = (
                session.query(DeletionOperation)
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
                    "chunk_count": len(json.loads(row.chunk_ids)),
                    "content_hash": row.content_hash,
                }
                for row in records
            ]

    def dashboard_data(self) -> dict[str, Any]:
        with self.session_factory() as session:
            documents = session.query(DocumentRecord).all()
            operations = (
                session.query(DeletionOperation)
                .order_by(DeletionOperation.created_at.desc(), DeletionOperation.id.desc())
                .all()
            )
            checks = session.query(DeletionCheck.status).all()
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
        hits = self.vector_store.query(query, limit)
        known_chunks: set[tuple[str, str]] = set()
        with self.session_factory() as session:
            records = (
                session.query(DocumentRecord)
                .filter(DocumentRecord.status != "deleted")
                .all()
            )
            for record in records:
                known_chunks.update((record.id, chunk_id) for chunk_id in json.loads(record.chunk_ids))
        return [
            hit
            for hit in hits
            if (hit["document_id"], hit["chunk_id"]) in known_chunks
        ]

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

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return f"{type(error).__name__}: operation failed; document content and credentials omitted."

    def delete_document(self, document_id: str, authorization_token: str | None) -> dict[str, Any]:
        with telemetry_span(
            "deletion.orchestration",
            kind="CHAIN",
            document_id=document_id,
            operation_type="deletion",
        ) as span:
            result = self._delete_document(document_id, authorization_token)
            span.set_result(
                result.get("status", "not_verified"),
                operation_id=result.get("operation_id"),
            )
            return result

    def _delete_document(self, document_id: str, authorization_token: str | None) -> dict[str, Any]:
        if not self.delete_auth_token or not authorization_token or not secrets.compare_digest(
            self.delete_auth_token.encode("utf-8"),
            authorization_token.encode("utf-8"),
        ):
            return {
                "document_id": document_id,
                "status": "blocked",
                "reason": "A configured deletion authorization token is required.",
            }

        with self.session_factory() as session:
            record = session.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
            if record is None:
                return {"document_id": document_id, "status": "not_verified", "reason": "Document not found."}
            if record.status == "deleted":
                completed_operation = (
                    session.query(DeletionOperation)
                    .filter(
                        DeletionOperation.document_id == document_id,
                        DeletionOperation.status == "pass",
                    )
                    .order_by(DeletionOperation.created_at.desc())
                    .first()
                )
                if completed_operation is not None:
                    return self.deletion_report(completed_operation.id) or {
                        "document_id": document_id,
                        "status": "not_verified",
                        "reason": "Completed deletion report was not found.",
                    }
            chunk_ids = json.loads(record.chunk_ids)
            original_content = record.content
            operation = DeletionOperation(
                id=str(uuid.uuid4()),
                document_id=document_id,
                chunk_ids=json.dumps(chunk_ids),
                status="running",
                created_at=self._timestamp(),
            )
            session.add(operation)
            session.commit()
            operation_id = operation.id
            source_file_path = record.source_file_path
            safe_filename = record.filename

        with self.session_factory() as session:
            operation = session.query(DeletionOperation).filter(DeletionOperation.id == operation_id).one()
            source_status = "blocked"
            source_result: dict[str, Any] = {"path_configured": bool(source_file_path)}
            source_error: str | None = None
            if source_file_path:
                try:
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
                    source_path_fingerprint = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
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
                        if (
                            prior_result.get("file_exists_after_delete") is False
                            and prior_result.get("source_path_fingerprint") == source_path_fingerprint
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
            self._record_check(
                session,
                operation,
                "source_file",
                "path.unlink_and_path.exists",
                source_status,
                source_result,
                source_error,
            )

            delete_status = "blocked"
            delete_result: dict[str, Any] = {"selected_chunk_ids": chunk_ids}
            delete_error: str | None = None
            try:
                delete_result.update(self.vector_store.delete_chunks(document_id, chunk_ids))
                delete_status = "pass"
            except Exception as error:
                delete_status = "fail"
                delete_error = self._safe_error(error)
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
            self._record_check(
                session,
                operation,
                "chromadb_api",
                "collection.get(where=document_id)",
                residual_status,
                residual_result,
                residual_error,
            )

            retrieval_status = "blocked"
            retrieval_result: dict[str, Any] = {}
            retrieval_error: str | None = None
            if original_content:
                try:
                    hits = self.vector_store.query(original_content[:1000], limit=10)
                    matching_ids = sorted(
                        hit["chunk_id"] for hit in hits if hit["document_id"] == document_id
                    )
                    retrieval_status = "fail" if matching_ids else "pass"
                    retrieval_result = {
                        "matching_chunk_ids": matching_ids,
                        "returned_document_ids": sorted({hit["document_id"] for hit in hits}),
                        "result_count": len(hits),
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
            if len(prior_checks) == 4 and prior_statuses == {"pass"}:
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

            checks = (
                session.query(DeletionCheck)
                .filter(DeletionCheck.operation_id == operation_id)
                .all()
            )
            required_statuses = {check.status for check in checks}
            if len(checks) == 5 and required_statuses == {"pass"}:
                operation.status = "pass"
                record.status = "deleted"
            elif "fail" in required_statuses:
                operation.status = "fail"
                record.status = "deletion_failed"
            else:
                operation.status = "blocked"
                record.status = "deletion_blocked"
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
            return {
                "operation_id": operation.id,
                "document_id": operation.document_id,
                "chunk_ids": json.loads(operation.chunk_ids),
                "status": operation.status,
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
                    "not_examined": [
                        "SSD remanence",
                        "filesystem journals",
                        "backups",
                        "storage outside configured application layers",
                    ],
                },
            }

    def verify_document(self, document_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            operation = (
                session.query(DeletionOperation)
                .filter(DeletionOperation.document_id == document_id)
                .order_by(DeletionOperation.created_at.desc())
                .first()
            )
            if operation is None:
                return {
                    "document_id": document_id,
                    "status": "not_verified",
                    "reason": "No persisted deletion operation exists for this document.",
                }
            operation_id = operation.id
        return self.deletion_report(operation_id) or {
            "document_id": document_id,
            "status": "not_verified",
            "reason": "Deletion operation report was not found.",
        }
