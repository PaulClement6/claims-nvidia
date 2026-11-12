from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from app.models import Claim, Customer, Policy
from app.store import Store


class NewClaim(BaseModel):
    customer_id: str
    policy_number: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.store = Store()  # reads CLAIMS_DB at startup
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="claims", lifespan=lifespan)

    def store(request: Request) -> Store:
        s: Store = request.app.state.store
        return s

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/customers")
    def customers(request: Request) -> list[Customer]:
        return store(request).get_customers()

    @app.get("/customers/{customer_id}/policies")
    def policies(customer_id: str, request: Request) -> list[Policy]:
        s = store(request)
        if not any(c.id == customer_id for c in s.get_customers()):
            raise HTTPException(404, "unknown customer")
        return s.get_customer_policies(customer_id)

    @app.post("/claims", status_code=201)
    def create_claim(body: NewClaim, request: Request) -> Claim:
        claim = store(request).create_claim(body.customer_id, body.policy_number)
        if claim is None:
            raise HTTPException(404, "policy not found for this customer")
        return claim

    @app.get("/claims/{claim_id}")
    def get_claim(claim_id: str, request: Request) -> Claim:
        claim = store(request).get_claim(claim_id)
        if claim is None:
            raise HTTPException(404, "unknown claim")
        return claim

    return app


app = create_app()
