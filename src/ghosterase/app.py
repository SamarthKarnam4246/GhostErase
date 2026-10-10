from __future__ import annotations

from fastapi import FastAPI, Form, HTTPException, UploadFile

from .service import GhostEraseService

app = FastAPI(title="GhostErase")
service = GhostEraseService()


@app.get("/health")
def health() -> dict:
    return service.health()


@app.post("/documents")
async def ingest_document(
    text: str = Form(default=""),
    filename: str = Form(default="document.txt"),
    source: str = Form(default="upload"),
    file: UploadFile | None = None,
) -> dict:
    payload = text
    if file is not None:
        payload = (await file.read()).decode("utf-8", errors="replace")
        filename = file.filename or filename
    if not payload.strip():
        raise HTTPException(status_code=400, detail="Document content cannot be empty.")
    return service.ingest_document(payload, source=source, filename=filename)


@app.post("/search")
def search(query: str = Form(...), limit: int = Form(default=5)) -> dict:
    return {"query": query, **service.search(query, limit=limit)}


@app.post("/delete")
def delete_document(document_id: str = Form(...)) -> dict:
    return service.delete_document(document_id)


@app.post("/verify")
def verify_document(document_id: str = Form(...)) -> dict:
    return service.verify_document(document_id)


@app.get("/report")
def report() -> dict:
    documents = service.storage.list_documents()
    return {"documents": documents}
