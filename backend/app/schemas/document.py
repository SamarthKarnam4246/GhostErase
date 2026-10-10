from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    text: str = Field(..., min_length=1)
    source: str = "upload"
    filename: str | None = None


class DocumentRead(BaseModel):
    id: str
    filename: str | None = None
    source: str
    content: str
    status: str
