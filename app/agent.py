"""The DejaVu agent: recall -> reason -> guardrails -> (analyst decides) -> retain."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from . import guardrails, prompts
from .config import Settings
from .llm import GroqJSONClient, LLMError
from .models import (AskResult, Diagnosis, Evidence, MemoryItem, PrecheckRequest, PrecheckResult,
                     ResolveRequest, ResolveResult)
from .store import Store
from .taxonomy import BY_CODE, UNKNOWN, normalize_code

log = logging.getLogger("dejavu.agent")


# ---------------------------------------------------------------------------
# Reasoners: the part that turns (case, memories) into a structured diagnosis
# ---------------------------------------------------------------------------

class LLMReasoner:
    name = "groq"

    def __init__(self, settings: Settings):
        self.client = GroqJSONClient(settings)

    async def diagnose(self, case: dict[str, Any], memories: Optional[list[MemoryItem]]) -> tuple[dict, str]:
        user = prompts.diagnosis_user_prompt(case, memories)
        return await self.client.complete_json(prompts.DIAGNOSIS_SYSTEM, user, prompts.DIAGNOSIS_SCHEMA, "diagnosis",
                                               max_completion_tokens=1100)


class HeuristicReasoner:
    """Offline stand-in used by tests and no-key development. Not an AI model."""

    name = "offline-heuristic"
    GENERIC = {
        "AC01": "CLIENT_TYPO_IN_DETAILS", "AC04": "BENEFICIARY_ACCOUNT_CLOSED", "AM05": "POSSIBLE_DUPLICATE_VERIFY",
        "BE01": "CLIENT_TYPO_IN_DETAILS", "RC01": "CLIENT_TYPO_IN_DETAILS", "RR04": "PURPOSE_CODE_MISSING",
        "SCREENING_HOLD": "SANCTIONS_POTENTIAL_MATCH", "NON_RECEIPT_CLAIM": "INTERMEDIARY_DELAY",
    }

    async def diagnose(self, case: dict[str, Any], memories: Optional[list[MemoryItem]]) -> tuple[dict, str]:
        code = case.get("reason_code") or case["exception_type"]
        bank_tag = f"bank:{case['payment']['creditor_bank']['code']}"
        code_tag = f"code:{code}"
        for m in memories or []:
            if bank_tag in m.tags and code_tag in m.tags and m.root_cause:
                return {
                    "root_cause": m.root_cause, "confidence": 0.85,
                    "reasoning": f"Same bank and same error as past case {m.case_id}, which was {m.root_cause}.",
                    "evidence": [{"memory_ref": m.ref, "why_relevant": "same bank and error code"}],
                    "recommended_action": BY_CODE[m.root_cause].default_fix if m.root_cause in BY_CODE else "",
                    "draft_message": "Offline mode draft.", "prevention_tip": "See playbook.",
                }, self.name
        if code == "AM04":
            rc = "CLIENT_INSUFFICIENT_FUNDS" if case.get("debtor_balance_check") == "failed" else "NOSTRO_FUNDING_SHORTFALL"
        else:
            rc = self.GENERIC.get(code, UNKNOWN)
        return {
            "root_cause": rc, "confidence": 0.5, "reasoning": "No matching history; generic interpretation of the error code.",
            "evidence": [], "recommended_action": BY_CODE[rc].default_fix if rc in BY_CODE else "Investigate manually.",
            "draft_message": "Offline mode draft.", "prevention_tip": "",
        }, self.name


def build_reasoner(settings: Settings, backend: str):
    return LLMReasoner(settings) if backend == "groq" else HeuristicReasoner()


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class DejaVuAgent:
    def __init__(self, settings: Settings, memory: Any, reasoner: Any, store: Optional[Store] = None):
        self.settings = settings
        self.memory = memory
        self.reasoner = reasoner
        self.store = store

    async def diagnose(self, case: dict[str, Any], use_memory: bool = True) -> Diagnosis:
        t0 = time.perf_counter()
        warnings: list[str] = []
        degraded = False
        memories: Optional[list[MemoryItem]] = None

        if use_memory:
            try:
                memories = await self.memory.recall_for_case(case)
            except Exception as exc:  # memory down must not block the analyst
                log.warning("recall failed for %s: %s", case["case_id"], exc)
                warnings.append("Memory is unavailable right now, so this diagnosis uses no history.")
                memories, degraded = [], True

        try:
            raw, model = await self.reasoner.diagnose(case, memories)
        except LLMError as exc:
            log.error("reasoning failed for %s: %s", case["case_id"], exc)
            raw, model, degraded = _manual_review(str(exc)), "none", True
            warnings.append("The AI model could not produce a valid answer. Please investigate manually.")

        raw["root_cause"] = normalize_code(raw.get("root_cause"))
        refs = {m.ref: m for m in memories or []}
        evidence = []
        for ev in raw.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            m = refs.get(str(ev.get("memory_ref", "")).strip())
            if m is None:
                continue  # drop hallucinated references
            evidence.append(Evidence(memory_ref=m.ref, why_relevant=str(ev.get("why_relevant", "")), case_id=m.case_id,
                                     text=m.text, occurred=m.occurred, type=m.type))
        raw["evidence"] = [e.model_dump() for e in evidence]
        raw = guardrails.apply(case, raw, used_memory=use_memory and bool(memories),
                               recalled_texts=[m.text for m in memories or []])
        kept = {e.get("memory_ref") for e in raw.get("evidence") or []}
        evidence = [e for e in evidence if e.memory_ref in kept]  # guardrails may reject evidence that does not fit

        rc = raw["root_cause"]
        diag = Diagnosis(
            case_id=case["case_id"], used_memory=use_memory, root_cause=rc,
            root_cause_label=BY_CODE[rc].label if rc in BY_CODE else "Needs human review",
            confidence=raw["confidence"], reasoning=str(raw.get("reasoning", "")),
            recommended_action=str(raw.get("recommended_action", "")), draft_message=str(raw.get("draft_message", "")),
            prevention_tip=str(raw.get("prevention_tip", "")), evidence=evidence, recalled=memories or [],
            requires_human_approval=raw["requires_human_approval"], auto_fix_eligible=raw["auto_fix_eligible"],
            guardrail_notes=raw["guardrail_notes"], model=model, degraded=degraded, warnings=warnings,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        if self.store:
            self.store.log_diagnosis(case["case_id"], use_memory, rc, diag.confidence, diag.model_dump())
        return diag

    async def resolve(self, case: dict[str, Any], req: ResolveRequest) -> ResolveResult:
        rc = normalize_code(req.root_cause)
        if rc == UNKNOWN:
            raise ValueError(f"Unknown root cause: {req.root_cause}")
        agent_rc = normalize_code(req.agent_root_cause) if req.agent_root_cause else None
        if agent_rc == UNKNOWN:
            agent_rc = None
        payload = {"root_cause": rc, "note": req.note.strip(), "agent_root_cause": agent_rc,
                   "agent_confidence": req.agent_confidence, "resolved_by": req.resolved_by}
        if self.store:
            self.store.resolve_case(case["case_id"], payload)

        retained, queued, message = False, False, ""
        try:
            await self.memory.retain_case(case, rc, payload["note"], agent_rc, req.agent_confidence)
            retained = True
            message = "Saved to memory. DejaVu will recognise this next time."
            await self.flush_outbox()
        except Exception as exc:
            log.warning("retain failed for %s: %s", case["case_id"], exc)
            if self.store:
                self.store.enqueue_retain(case["case_id"], payload, str(exc))
                queued = True
            message = "Resolved. Memory is unreachable, so the lesson is queued and will be saved automatically."
        return ResolveResult(case_id=case["case_id"], root_cause=rc,
                             agent_was_right=(agent_rc == rc) if agent_rc else None,
                             retained=retained, queued_for_retry=queued, message=message)

    async def flush_outbox(self) -> int:
        if not self.store:
            return 0
        done = 0
        for row in self.store.pending_retains():
            case = self.store.get_case(row["case_id"])
            if not case:
                self.store.outbox_done(row["id"])
                continue
            p = row["payload"]
            try:
                await self.memory.retain_case(case, p["root_cause"], p["note"], p.get("agent_root_cause"),
                                              p.get("agent_confidence"))
                self.store.outbox_done(row["id"])
                done += 1
            except Exception as exc:
                self.store.outbox_failed(row["id"], str(exc))
                break
        return done

    async def ask(self, question: str) -> AskResult:
        return await self.memory.ask(question)

    async def precheck(self, req: PrecheckRequest) -> PrecheckResult:
        return await self.memory.precheck(req)


def _manual_review(error: str) -> dict[str, Any]:
    return {
        "root_cause": UNKNOWN, "confidence": 0.0,
        "reasoning": "DejaVu could not complete an automated diagnosis for this case. " + error[:200],
        "evidence": [], "recommended_action": "Investigate manually and record the resolution so DejaVu learns it.",
        "draft_message": "", "prevention_tip": "",
    }
