"""FastAPI app: the exceptions desk API + the single-page UI."""

from __future__ import annotations

import asyncio
import json
import logging
import re
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
                     ResolveRequest, ResolveResult, SimSendRequest, SimSendResult)
from .simulator import ISO_STATUS, PaymentNetwork, case_id_prefix, new_payment_ref
from .store import Store
from .taxonomy import ROOT_CAUSES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dejavu")

STATIC = Path(__file__).parent / "static"
STATE: dict[str, Any] = {}
SIM_ID = re.compile(r"^EXC-\d{6}-S\d+$")  # simulated payments' case IDs


def _load_json(name: str) -> Any:
    return json.loads((settings.data_dir / name).read_text(encoding="utf-8"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    memory_backend, llm_backend = settings.resolved_modes()
    store = Store(settings.db_path)
    store.load_if_empty(_load_json("history.json"), _load_json("live.json"))
    memory = build_memory(settings, memory_backend)
    agent = DejaVuAgent(settings, memory, build_reasoner(settings, llm_backend), store)
    rules = settings.data_dir / "network_rules.json"
    network = PaymentNetwork(rules, _load_json("entities.json")) if rules.exists() else None
    STATE.update(store=store, memory=memory, agent=agent, network=network, memory_backend=memory_backend,
                 llm_backend=llm_backend, setup_error=None, seeding=None)
    log.info("DejaVu starting: memory=%s llm=%s bank=%s", memory_backend, llm_backend, memory.bank_id)
    try:
        await memory.setup()
        flushed = await agent.flush_outbox()
        if flushed:
            log.info("flushed %s queued retains", flushed)
        await _tidy_memory()
    except Exception as exc:  # the UI shows this; the desk still works without memory
        STATE["setup_error"] = str(exc)[:500]
        log.error("memory setup failed: %s", exc)
    seeding = asyncio.create_task(_seed_history()) if settings.seed_history and not STATE["setup_error"] else None
    yield
    if seeding and not seeding.done():
        seeding.cancel()
    await memory.close()


async def _tidy_memory() -> None:
    """Forget lessons from demo cases this desk no longer holds as resolved: simulated payments whose case is gone
    (a hosted copy loses its database when it restarts) and demo cases that are open again."""
    store, memory = STATE["store"], STATE["memory"]
    try:
        docs = await memory.document_ids()
    except Exception as exc:
        log.warning("could not list memory documents: %s", exc)
        return
    known = {c["case_id"]: c for c in store.list_cases()}
    stale = sorted(d for d in docs if (SIM_ID.match(d) and d not in known) or (
        d in known and known[d]["source"] in ("live", "sim") and known[d]["status"] == "open"))
    for doc_id in stale:
        try:
            await memory.forget_case(doc_id)
        except Exception as exc:
            log.warning("could not forget %s: %s", doc_id, exc)
    if stale:
        log.info("forgot %s stale demo documents: %s", len(stale), ", ".join(stale))


async def _seed_history() -> None:
    """Load the resolved history into an empty or partly loaded bank, one case at a time, in the background."""
    memory = STATE["memory"]
    history = _load_json("history.json")
    try:
        have = await memory.document_ids()
    except Exception as exc:  # cannot tell what is there: load everything (a retain replaces the same document)
        log.warning("could not list memory documents (%s); loading the whole history", exc)
        have = set()
    todo = [c for c in history if c["case_id"] not in have]
    STATE["seeding"] = {"done": 0, "total": len(todo), "running": bool(todo), "error": None}
    for i, case in enumerate(todo, 1):
        gt = case["ground_truth"]
        try:
            await memory.retain_case(case, gt["root_cause"], gt["resolution_note"])
        except Exception as exc:
            log.warning("seeding %s failed: %s", case["case_id"], exc)
            STATE["seeding"]["error"] = str(exc)[:200]
        STATE["seeding"]["done"] = i
    STATE["seeding"]["running"] = False
    if todo:
        log.info("history seeding finished: %s cases retained", len(todo))


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
        "seeding": STATE.get("seeding"), **_llm_capacity(),
    }


@app.get("/api/health")
async def health() -> dict[str, bool]:
    """Cheap liveness check for hosting platforms (touches neither memory nor the model)."""
    return {"ok": True}


def _llm_capacity() -> dict[str, Any]:
    """How many Groq keys are configured and how busy each one is (never the keys themselves)."""
    groq = getattr(getattr(STATE.get("agent"), "reasoner", None), "client", None)
    limiters = getattr(groq, "limiters", None) or []
    return {"llm_keys": len(limiters), "llm_tokens_last_minute": [lim.load() for lim in limiters],
            "llm_tpm_limit": settings.llm_tpm_limit}


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
    return {"available": True, **json.loads(path.read_text(encoding="utf-8"))}


@app.post("/api/admin/reset-demo")
async def reset_demo() -> dict[str, Any]:
    """Re-open the live demo cases, remove simulated payments, and delete what memory learned from both,
    so the demo can be rehearsed from the same starting point."""
    store, memory = STATE["store"], STATE["memory"]
    ids = store.reopen_live_cases()
    sim_ids = store.delete_cases("sim")
    live_ids = {c["case_id"] for c in store.list_cases() if c["source"] == "live"}
    try:  # also catches lessons from simulated payments this desk no longer lists (e.g. after a hosted restart)
        targets = sorted(d for d in await memory.document_ids() if SIM_ID.match(d) or d in live_ids)
    except Exception as exc:
        log.warning("could not list memory documents: %s", exc)
        targets = sorted(set(ids + sim_ids))
    forgotten = 0
    for cid in targets:
        try:
            await memory.forget_case(cid)
            forgotten += 1
        except Exception as exc:
            log.warning("could not forget %s: %s", cid, exc)
    return {"reopened": len(ids), "simulated_removed": len(sim_ids), "forgotten_in_memory": forgotten}


# ---- payment network simulator (live demo) ------------------------------------------------------------------
def _network() -> PaymentNetwork:
    if not STATE.get("network"):
        raise HTTPException(404, "The payment network simulator is not set up (data/network_rules.json is missing).")
    return STATE["network"]


@app.get("/api/sim/network")
async def sim_network() -> dict[str, Any]:
    """Banks, clients and intermediaries the simulated network knows. The banks' rules stay hidden."""
    return _network().directory()


@app.get("/api/sim/rules")
async def sim_rules() -> dict[str, Any]:
    """The hidden rulebook, so the presenter can show afterwards what DejaVu never saw."""
    return _network().rulebook()


@app.post("/api/sim/send", response_model=SimSendResult)
async def sim_send(req: SimSendRequest) -> SimSendResult:
    """Send a payment into the simulated network. A rejection or hold opens a normal exception on the desk."""
    network = _network()
    outcome = network.evaluate(req)
    ref = req.payment_ref or new_payment_ref()
    bank = network.bank(req.beneficiary_bank) or {"name": req.beneficiary_bank, "bic": ""}
    case_id = None
    if outcome.opens_exception:
        case_id = STATE["store"].next_case_id(case_id_prefix())
        STATE["store"].add_case(network.build_case(req, outcome, case_id, ref), source="sim")
        log.info("simulated network: %s %s at %s -> exception %s", ref, outcome.status, bank["name"], case_id)
    return SimSendResult(payment_ref=ref, status=outcome.status, iso_status=ISO_STATUS[outcome.status],
                         bank=bank["name"], bic=bank.get("bic", ""), message=outcome.message,
                         reason_code=outcome.code, reason_text=outcome.text, case_id=case_id)
