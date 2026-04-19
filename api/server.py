from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.data_loader import load_dataset, normalize_ticket
from agent.env_bootstrap import load_app_env
from agent.runner import run_batch

load_app_env()

ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = ROOT / "web" / "dist"


class RunRequest(BaseModel):
    simulate_faults: bool = False


class EventHub:
    """Broadcast JSON events to all connected WebSocket clients."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._clients:
                self._clients.remove(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        text = json.dumps(event, default=str)
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


hub = EventHub()
_run_lock = asyncio.Lock()

app = FastAPI(title="ShopWave Autonomous Agent", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "shopwave-agent"}


@app.get("/api/meta")
async def meta() -> dict[str, Any]:
    dataset = load_dataset(ROOT)
    tickets_raw = dataset["tickets"]
    return {
        "ticket_count": len(tickets_raw),
        "dataset": "agentic_ai_hackthon_2026_sample_data-main",
        "features": [
            "parallel_ticket_processing",
            "tool_chain_audit",
            "retry_with_backoff",
            "schema_validation",
            "structured_escalation",
            "optional_llm_triage_openai_or_ollama",
        ],
    }


@app.get("/api/tickets")
async def list_tickets() -> dict[str, Any]:
    dataset = load_dataset(ROOT)
    normalized = [normalize_ticket(t) for t in dataset["tickets"]]
    brief = []
    for t in normalized:
        brief.append(
            {
                "ticket_id": t.ticket_id,
                "email": t.email,
                "preview": (t.message[:160] + "…") if len(t.message) > 160 else t.message,
                "order_id": t.order_id,
            }
        )
    return {"tickets": brief}


@app.post("/api/run")
async def trigger_run(body: RunRequest) -> dict[str, bool]:
    faults = body.simulate_faults

    async def sink(event: dict[str, Any]) -> None:
        await hub.broadcast(event)

    async def job() -> None:
        async with _run_lock:
            try:
                await run_batch(ROOT, simulate_faults=faults, event_sink=sink)
            except Exception as exc:
                await hub.broadcast({"type": "run_error", "message": str(exc)})

    asyncio.create_task(job())
    return {"started": True}


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws)


if WEB_DIST.is_dir() and (WEB_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="spa")
else:

    @app.get("/")
    async def root_build_hint() -> JSONResponse:
        return JSONResponse(
            {
                "message": "Dashboard UI not built yet.",
                "steps": ["cd web", "npm install", "npm run build"],
                "then": "uvicorn api.server:app --reload",
                "api_docs": "/docs",
            }
        )
