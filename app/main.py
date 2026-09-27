"""FastAPI app: the exceptions desk API + the single-page UI."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import DejaVuAgent, build_reasoner
from .config import settings
from .memory import build_memory
from .models import (AskRequest, AskResult, DiagnoseRequest, Diagnosis, PrecheckRequest, PrecheckResult,
                     ResolveRequest, ResolveResult)
from .store import Store
from .taxonomy import ROOT_CAUSES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dejavu")

STATIC = Path(__file__).parent / "static"
STATE: dict[str, Any] = {}


def _load_json(name: str) -> Any:
    return json.loads((settings.data_dir / name).read_text())


@asynccontextmanager
async def lifespan(app: FastAPI):
    memory_backend, llm_backend = settings.resolved_modes()
    store = Store(settings.db_path)
    store.load_if_empty(_load_json("history.json"), _load_json("live.json"))
    memory = build_memory(settings, memory_backend)
    agent = DejaVuAgent(settings, memory, build_reasoner(settings, llm_backend), store)
    STATE.update(store=store, memory=memory, agent=agent, memory_backend=memory_backend,
                 llm_backend=llm_backend, setup_error=None)
    log.info("DejaVu starting: memory=%s llm=%s bank=%s", memory_backend, llm_backend, memory.bank_id)
    try:
        await memory.setup()
        flushed = await agent.flush_outbox()
        if flushed:
            log.info("flushed %s queued retains", flushed)
    except Exception as exc:  # the UI shows this; the desk still works without memory
        STATE["setup_error"] = str(exc)[:500]
        log.error("memory setup failed: %s", exc)
    yield
    await memory.close()


app = FastAPI(title="DejaVu - payment exceptions agent", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _public_case(case: dict[str, Any]) -> dict[str, Any]:
    """Strip ground truth before a case leaves the server (optionally keep a presenter hint)."""
    out = {k: v for k, v in case.items() if k != "ground_truth"}
    gt = case.get("ground_truth") or {}
    if case.get("source") == "history" and not case.get("resolution"):
        # historical cases were resolved before DejaVu existed; show the analyst's record
        out["resolution"] = {"root_cause": gt.get("root_cause"), "note": gt.get("resolution_note"), "historical": True}
    if settings.show_demo_hints and case.get("status") == "open":
        out["demo_hint"] = {"root_cause": gt.get("root_cause"), "note": gt.get("resolution_note")}
    return out


def _get_case(case_id: str) -> dict[str, Any]:
    case = STATE["store"].get_case(case_id)
    if not case:
        raise HTTPException(404, f"Case {case_id} not found")
    return case


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    memory = STATE["memory"]
    try:
        counts = await memory.stats()
    except Exception as exc:
        counts = {"error": str(exc)[:200]}
    return {
        "memory_backend": STATE["memory_backend"], "llm_backend": STATE["llm_backend"],
        "offline": STATE["memory_backend"] != "hindsight" or STATE["llm_backend"] != "groq",
        "bank_id": memory.bank_id, "model": settings.llm_model if STATE["llm_backend"] == "groq" else "offline-heuristic",
        "setup_error": STATE.get("setup_error"), "memory_counts": counts, "cases": STATE["store"].counts(),
    }


@app.post("/api/setup")
async def rerun_setup() -> dict[str, Any]:
    try:
        info = await STATE["memory"].setup()
        STATE["setup_error"] = None
        return {"ok": True, **info}
    except Exception as exc:
        STATE["setup_error"] = str(exc)[:500]
        raise HTTPException(502, f"Memory setup failed: {exc}")


@app.get("/api/taxonomy")
async def taxonomy() -> list[dict[str, Any]]:
    return [{"code": rc.code, "label": rc.label, "description": rc.description, "default_fix": rc.default_fix,
             "human_only": rc.human_only} for rc in ROOT_CAUSES]


@app.get("/api/entities")
async def entities() -> dict[str, Any]:
    return _load_json("entities.json")


@app.get("/api/cases")
async def list_cases(status: Optional[str] = None) -> list[dict[str, Any]]:
    if status not in (None, "open", "resolved"):
        raise HTTPException(400, "status must be open or resolved")
    return [_public_case(c) for c in STATE["store"].list_cases(status)]


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str) -> dict[str, Any]:
    return _public_case(_get_case(case_id))


@app.post("/api/cases/{case_id}/diagnose", response_model=Diagnosis)
async def diagnose(case_id: str, req: DiagnoseRequest) -> Diagnosis:
    return await STATE["agent"].diagnose(_get_case(case_id), use_memory=req.use_memory)


@app.post("/api/cases/{case_id}/resolve", response_model=ResolveResult)
async def resolve(case_id: str, req: ResolveRequest) -> ResolveResult:
    case = _get_case(case_id)
    if case["status"] != "open":
        raise HTTPException(409, "Case is already resolved")
    try:
        return await STATE["agent"].resolve(case, req)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/memory/lessons")
async def lessons(limit: int = 30) -> dict[str, Any]:
    try:
        return {"items": await STATE["memory"].lessons(limit=max(1, min(limit, 100)))}
    except Exception as exc:
        raise HTTPException(502, f"Memory unavailable: {exc}")


@app.get("/api/memory/playbook")
async def playbook() -> dict[str, Any]:
    try:
        return await STATE["memory"].playbook()
    except Exception as exc:
        raise HTTPException(502, f"Memory unavailable: {exc}")


@app.post("/api/memory/playbook/refresh")
async def refresh_playbook() -> dict[str, Any]:
    try:
        return await STATE["memory"].refresh_playbook()
    except Exception as exc:
        raise HTTPException(502, f"Memory unavailable: {exc}")


@app.post("/api/memory/ask", response_model=AskResult)
async def ask(req: AskRequest) -> AskResult:
    try:
        return await STATE["agent"].ask(req.question)
    except Exception as exc:
        raise HTTPException(502, f"Memory unavailable: {exc}")


@app.post("/api/precheck", response_model=PrecheckResult)
async def precheck(req: PrecheckRequest) -> PrecheckResult:
    try:
        return await STATE["agent"].precheck(req)
    except Exception as exc:
        raise HTTPException(502, f"Memory unavailable: {exc}")


@app.get("/api/replay")
async def replay_results() -> dict[str, Any]:
    path = settings.data_dir / "replay_results.json"
    if not path.exists():
        return {"available": False}
    return {"available": True, **json.loads(path.read_text())}


@app.post("/api/admin/reset-demo")
async def reset_demo() -> dict[str, Any]:
    """Re-open the live demo cases and remove their lessons from memory, so the demo can be rehearsed."""
    ids = STATE["store"].reopen_live_cases()
    forgotten = 0
    for cid in ids:
        try:
            await STATE["memory"].forget_case(cid)
            forgotten += 1
        except Exception as exc:
            log.warning("could not forget %s: %s", cid, exc)
    return {"reopened": len(ids), "forgotten_in_memory": forgotten}
