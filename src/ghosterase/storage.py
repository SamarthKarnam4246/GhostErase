from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import settings


class SQLiteStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filename TEXT,
                source TEXT,
                content TEXT,
                status TEXT DEFAULT 'stored',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                deleted_at TEXT
            )
            """
        )
        self._connection.commit()

    def add_document(self, document_id: str, filename: str | None, source: str, content: str) -> dict[str, Any]:
        self._connection.execute(
            "INSERT OR REPLACE INTO documents (id, filename, source, content, status) VALUES (?, ?, ?, ?, 'stored')",
            (document_id, filename, source, content),
        )
        self._connection.commit()
        return self.get_document(document_id)

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def delete_document(self, document_id: str) -> dict[str, Any] | None:
        row = self.get_document(document_id)
        if row is None:
            return None
        self._connection.execute(
            "UPDATE documents SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (document_id,),
        )
        self._connection.commit()
        return self.get_document(document_id)

    def list_documents(self) -> list[dict[str, Any]]:
        rows = self._connection.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]
