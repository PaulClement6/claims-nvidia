import os
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.agent import Agent, get_agent
from app.docintel.reader import get_reader
from app.models import Claim, Customer, Document, Message, Policy
from app.store import Store
from app.stream import event_stream
from app.tools import checks as checks_tool
from app.tools import claims as claims_tool
from app.tools import documents as documents_tool
from app.tools import policies as policies_tool
from app.tools import requirements as requirements_tool
from app.tools import routing as routing_tool
from app.tools.gateway import Gateway
from app.views import CustomerView, customer_view

MAX_UPLOAD = 10 * 1024 * 1024
# magic bytes -> extension; the client file name and content type are never trusted
MAGIC = {b"%PDF-": ".pdf", b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg"}
MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}


class NewClaim(BaseModel):
    customer_id: str


class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


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
    claims_tool.register(gateway)
    checks_tool.register(gateway, uploads)
    requirements_tool.register(gateway)
    routing_tool.register(gateway)
    app.state.agent = get_agent(gateway)  # CLAIMS_MODE: local or nim
    app.state.uploads = uploads
    app.state.store = store
    app.state.gateway = gateway
    app.state.locks = {}
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

    @contextmanager
    def claim_lock(request: Request, claim_id: str) -> Iterator[None]:
        """One turn at a time per claim: a message, an upload or a policy change.

        ponytail: one lock per claim id, never freed (a few bytes per claim).
        ponytail: in `nim` mode a turn holds the lock for its model calls, at most 12
        of up to 60 s (a timeout), so worst case minutes for that claim only; one
        demo visitor types one message at a time. Other claims run in the thread
        pool. Take the model call out of the lock if one claim ever has two writers.
        """
        locks: dict[str, threading.Lock] = request.app.state.locks
        with locks.setdefault(claim_id, threading.Lock()):
            yield

    def run_agent(
        request: Request, claim_id: str, text: str | None, document_id: str | None
    ) -> list[Message]:
        """The agent answers; each answer is stored and shown in the pipeline."""
        agent: Agent = request.app.state.agent
        out = []
        for reply in agent.handle(claim_id, text, document_id):
            store(request).append_event(
                claim_id, "agent", "message", reply[:80], outputs={"text": reply}
            )
            out.append(store(request).add_message(claim_id, "agent", reply))
        return out

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/customers")
    def customers(request: Request) -> list[Customer]:
        return store(request).get_customers()

    @app.post("/claims", status_code=201)
    def create_claim(body: NewClaim, request: Request) -> CustomerView:
        claim = store(request).create_claim(body.customer_id)
        if claim is None:
            raise HTTPException(404, "unknown customer")
        return customer_view(claim)

    @app.get("/claims/{claim_id}")
    def get_claim(claim_id: str, request: Request) -> Claim:
        """The pipeline view: everything. The customer panel never calls it."""
        return claim_or_404(request, claim_id)

    @app.get("/claims/{claim_id}/customer")
    def get_customer_view(claim_id: str, request: Request) -> CustomerView:
        return customer_view(claim_or_404(request, claim_id))

    @app.get("/claims/{claim_id}/policies")
    def claim_policies(claim_id: str, request: Request) -> list[Policy]:
        claim = claim_or_404(request, claim_id)
        gateway: Gateway = request.app.state.gateway
        out = gateway.call(
            claim_id, "get_customer_policies", {"customer_id": claim.customer.id}
        )
        return [Policy.model_validate(p) for p in out["policies"]]

    @app.put("/claims/{claim_id}/policy")
    def set_policy(claim_id: str, body: ClaimPolicy, request: Request) -> CustomerView:
        claim_or_404(request, claim_id)
        with claim_lock(request, claim_id):
            claim = store(request).set_claim_policy(claim_id, body.policy_number)
            if claim is None:
                raise HTTPException(404, "policy not found for this customer")
            if not store(request).get_messages(claim_id):
                run_agent(request, claim_id, None, None)  # the greeting
                claim = claim_or_404(request, claim_id)
            return customer_view(claim)

    @app.get("/claims/{claim_id}/messages")
    def get_messages(claim_id: str, request: Request) -> list[Message]:
        claim_or_404(request, claim_id)
        return store(request).get_messages(claim_id)

    @app.post("/claims/{claim_id}/messages")
    def post_message(
        claim_id: str, body: NewMessage, request: Request
    ) -> list[Message]:
        with claim_lock(request, claim_id):
            if claim_or_404(request, claim_id).policy is None:
                raise HTTPException(409, "pick a policy first")
            text = body.text.strip()
            if not text:
                raise HTTPException(422, "empty message")
            store(request).add_message(claim_id, "customer", text)
            return run_agent(request, claim_id, text, None)

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
        with claim_lock(request, claim_id):
            if claim_or_404(request, claim_id).agent_state == "closed":
                raise HTTPException(409, "the claim is closed")
            path: Path = request.app.state.uploads / doc.file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            store(request).save_document(claim_id, doc)
            name = doc.file_metadata["original_name"] or "a file"
            store(request).add_message(claim_id, "customer", f"Uploaded {name}")
            run_agent(request, claim_id, None, doc.id)
            found = store(request).find_document(doc.id)
            return found[1] if found else doc

    @app.get("/claims/{claim_id}/documents/{document_id}/file")
    def document_file(
        claim_id: str, document_id: str, request: Request
    ) -> FileResponse:
        """The stored file, for the pipeline panel. The path comes from the stored
        document of this claim (its id and extension), never from the request."""
        doc = next(
            (
                d
                for d in claim_or_404(request, claim_id).documents
                if d.id == document_id
            ),
            None,
        )
        path: Path = request.app.state.uploads / doc.file if doc else Path()
        if doc is None or not path.is_file():
            raise HTTPException(404, "unknown document")
        return FileResponse(
            path,
            media_type=MEDIA_TYPES[path.suffix],
            headers={"X-Content-Type-Options": "nosniff"},
        )

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
