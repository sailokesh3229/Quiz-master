"""Section 3, task 1: FastAPI app entry point. Wires up CORS (for the
Vite dev server / deployed frontend origin), a health-check endpoint that
proves DB connectivity end-to-end, and every router."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import load_dotenv
from app.db import get_connection
from app.routers import catalog, performance, profile, quiz

load_dotenv()

app = FastAPI(title="Quiz Master API")

# Comma-separated, so local dev and the deployed frontend can both be allowed.
_frontend_origins = [
    o.strip().rstrip("/")
    for o in os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(profile.router)
app.include_router(catalog.router)
app.include_router(quiz.router)
app.include_router(performance.router)


@app.get("/api/health")
def health():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        row = cur.fetchone()
    return {"status": "ok", "db": row["ok"] == 1}
