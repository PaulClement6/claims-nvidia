from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.models import Claim, Customer, Policy
from app.store import Store
from app.stream import event_stream
from app.tools import policies as policies_tool
from app.tools.gateway import Gateway


class NewClaim(BaseModel):
    customer_id: str


class ClaimPolicy(BaseModel):
    policy_number: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = Store()  # reads CLAIMS_DB at startup
    gateway = Gateway(store)
    policies_tool.register(gateway)
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
