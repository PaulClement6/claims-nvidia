import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pypdf import PdfReader

from app.docintel.reader import get_reader
from app.models import Claim, Customer, Document, Extraction, Policy
from app.store import Store
from app.stream import event_stream
from app.tools import documents as documents_tool
from app.tools import policies as policies_tool
from app.tools.gateway import Gateway

MAX_UPLOAD = 10 * 1024 * 1024
# magic bytes -> extension; the client file name and content type are never trusted
MAGIC = {b"%PDF-": ".pdf", b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg"}


class NewClaim(BaseModel):
    customer_id: str


class ClaimPolicy(BaseModel):
    policy_number: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = Store()  # reads CLAIMS_DB at startup
    gateway = Gateway(store)
    policies_tool.register(gateway)
    reader = get_reader()  # unknown CLAIMS_READER stops the startup
    uploads = Path(os.environ.get("CLAIMS_UPLOADS", "uploads"))
    documents_tool.register(gateway, reader, uploads)
    app.state.uploads = uploads
    app.state.store = store
    app.state.gateway = gateway
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="claims", lifespan=lifespan)

    def store(request: Request) -> Store:
        s: Store = request.app.state.store
        return s

    def claim_or_404(request: Request, claim_id: str) -> Claim:
        claim = store(request).get_claim(claim_id)
        if claim is None:
            raise HTTPException(404, "unknown claim")
        return claim

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/customers")
    def customers(request: Request) -> list[Customer]:
        return store(request).get_customers()

    @app.post("/claims", status_code=201)
    def create_claim(body: NewClaim, request: Request) -> Claim:
        claim = store(request).create_claim(body.customer_id)
        if claim is None:
            raise HTTPException(404, "unknown customer")
        return claim

    @app.get("/claims/{claim_id}")
    def get_claim(claim_id: str, request: Request) -> Claim:
        return claim_or_404(request, claim_id)

    @app.get("/claims/{claim_id}/policies")
    def claim_policies(claim_id: str, request: Request) -> list[Policy]:
        claim = claim_or_404(request, claim_id)
        gateway: Gateway = request.app.state.gateway
        out = gateway.call(
            claim_id, "get_customer_policies", {"customer_id": claim.customer.id}
        )
        return [Policy.model_validate(p) for p in out["policies"]]

    @app.put("/claims/{claim_id}/policy")
    def set_policy(claim_id: str, body: ClaimPolicy, request: Request) -> Claim:
        claim_or_404(request, claim_id)
        claim = store(request).set_claim_policy(claim_id, body.policy_number)
        if claim is None:
            raise HTTPException(404, "policy not found for this customer")
        return claim

    @app.post("/claims/{claim_id}/documents", status_code=201)
    def upload_document(claim_id: str, request: Request, file: UploadFile) -> Document:
        claim_or_404(request, claim_id)
        data = file.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file larger than 10 MB")
        ext = next((e for m, e in MAGIC.items() if data.startswith(m)), None)
        if ext is None:
            raise HTTPException(415, "only PDF, PNG and JPEG files are accepted")
        pages = 1
        if ext == ".pdf":
            try:
                pages = max(1, len(PdfReader(BytesIO(data)).pages))
            except Exception:
                raise HTTPException(415, "the PDF cannot be read") from None
        doc_id = f"D-{uuid.uuid4().hex[:8]}"
        doc = Document(
            id=doc_id,
            file=f"{claim_id}/{doc_id}{ext}",
            pages=pages,
            file_metadata={"original_name": (file.filename or "")[:200]},
        )
        path: Path = request.app.state.uploads / doc.file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        store(request).save_document(claim_id, doc)
        gateway: Gateway = request.app.state.gateway
        doc.document_type = gateway.call(
            claim_id, "classify_document", {"document_id": doc.id}
        )["document_type"]
        out = Extraction.model_validate(
            gateway.call(claim_id, "extract_document", {"document_id": doc.id})
        )
        doc.document_type = out.document_type.value
        doc.fields, doc.line_items = out.fields, out.line_items
        store(request).save_document(claim_id, doc)
        return doc

    @app.get("/claims/{claim_id}/events")
    def events(
        claim_id: str,
        request: Request,
        after: int | None = Query(None, ge=0),
        last_event_id: int | None = Header(None, ge=0),
    ) -> StreamingResponse:
        claim_or_404(request, claim_id)
        start = after if after is not None else last_event_id or 0
        return StreamingResponse(
            event_stream(store(request), claim_id, start, request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
