from fastapi import FastAPI

app = FastAPI(title="claims")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
