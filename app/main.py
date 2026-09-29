"""Paper desk HTTP server."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .hub import HUB

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="ETF T+0 纸面台", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=HUB.bootstrap, name="etf-bootstrap", daemon=True).start()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/desk")
def desk() -> dict:
    return HUB.snapshot()


@app.post("/api/refresh")
def refresh() -> dict:
    if not HUB.ready:
        return HUB.snapshot()
    return HUB.refresh_now()
