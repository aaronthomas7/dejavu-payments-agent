"""DejaVu's memory layer.

`HindsightMemory` is the real thing and what the demo runs on:

    retain   - every resolved exception (and whether DejaVu got it right) is written to the bank
    recall   - before diagnosing, fetch similar past cases + consolidated lessons (observations)
    reflect  - "Ask DejaVu" and the pre-flight check reason over the whole bank, with directives applied
    observations   - Hindsight consolidates repeated cases into lessons automatically
    mental model   - the Payment Exception Playbook, a standing answer that refreshes itself
    directives     - hard rules (sanctions holds are human-only, mask account numbers, cite evidence)

`LocalMemory` is a tiny keyword-matching stand-in so tests and the UI can run with
no API keys. It is deliberately simple and is labelled "offline" everywhere it
shows up; it is not how the product learns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import prompts
from .config import Settings
from .models import AskResult, MemoryItem, PrecheckRequest, PrecheckResult, PrecheckWarning
from .taxonomy import BY_CODE

log = logging.getLogger("dejavu.memory")


class MemoryUnavailable(RuntimeError):
    pass


def _case_tags(case: dict[str, Any]) -> list[str]:
    p = case["payment"]
    return [
        f"bank:{p['creditor_bank']['code']}",
        f"client:{p['debtor']['client_id']}",
        f"code:{case.get('reason_code') or case['exception_type']}",
        f"ccy:{p['currency']}",
    ]


def _case_entities(case: dict[str, Any]) -> list[dict[str, str]]:
    p = case["payment"]
    ents = [
        {"text": p["creditor_bank"]["name"], "type": "bank"},
        {"text": p["debtor"]["name"], "type": "client"},
        {"text": p["creditor"]["name"], "type": "beneficiary"},
    ]
    if p.get("intermediary_bank"):
        ents.append({"text": p["intermediary_bank"]["name"], "type": "bank"})
    return ents


# ---------------------------------------------------------------------------
# Hindsight
# ---------------------------------------------------------------------------

class HindsightMemory:
    backend = "hindsight"

    def __init__(self, settings: Settings):
        from hindsight_client import Hindsight  # imported lazily so tests don't need network

        self.settings = settings
        self.bank_id = settings.bank_id
        self.client = Hindsight(base_url=settings.hindsight_base_url, api_key=settings.hindsight_api_key or None,
                                timeout=120.0)
        self.ready = False
        self.last_error: Optional[str] = None
        self._stats_cache: tuple[float, dict] | None = None
        self._rich_retain = True

    # ---- setup -------------------------------------------------------------
    async def setup(self) -> dict[str, Any]:
        """Idempotently create/configure the bank, its directives and the playbook mental model."""
        c, bank = self.client, self.bank_id
        await c.acreate_bank(bank_id=bank, name=prompts.BANK_NAME, background=prompts.BANK_BACKGROUND,
                             retain_mission=prompts.RETAIN_MISSION, reflect_mission=prompts.REFLECT_MISSION)
        try:
            await c.aupdate_bank_config(bank, **prompts.DISPOSITION)
        except Exception as exc:  # disposition is nice-to-have; never block startup on it
            log.warning("could not set disposition: %s", exc)

        existing = await c.alist_directives(bank)
        names = {d.name for d in getattr(existing, "items", None) or getattr(existing, "directives", None) or []}
        for d in prompts.DIRECTIVES:
            if d["name"] not in names:
                await c.acreate_directive(bank, name=d["name"], content=d["content"], priority=d["priority"])

        await self._ensure_playbook()
        self.ready = True
        self.last_error = None
        return {"bank_id": bank, "directives": [d["name"] for d in prompts.DIRECTIVES], "playbook": prompts.PLAYBOOK_ID}

    async def _ensure_playbook(self) -> None:
        from hindsight_client_api.exceptions import ApiException

        try:
            await self.client.aget_mental_model(self.bank_id, prompts.PLAYBOOK_ID, detail="metadata")
            return
        except ApiException as exc:
            if exc.status not in (404, 400):
                raise
        try:
            await self.client.acreate_mental_model(
                self.bank_id, name=prompts.PLAYBOOK_NAME, source_query=prompts.PLAYBOOK_QUERY,
                id=prompts.PLAYBOOK_ID, max_tokens=2048,
                trigger={"refresh_after_consolidation": True, "mode": "delta", "min_refresh_interval_seconds": 120},
            )
        except ApiException as exc:
            if exc.status != 409:  # already exists (race) is fine
                raise

    async def reset(self) -> None:
        """Delete the bank entirely (used by the replay script for a clean learning curve)."""
        from hindsight_client_api.exceptions import ApiException

        try:
            await self.client.adelete_bank(self.bank_id)
        except ApiException as exc:
            if exc.status != 404:
                raise
        self.ready = False
        await asyncio.sleep(1.0)
        await self.setup()

    async def count(self) -> int:
        resp = await self.client.alist_memories(self.bank_id, limit=1)
        return int(resp.total)

    async def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._stats_cache and now - self._stats_cache[0] < 15:
            return self._stats_cache[1]
        out: dict[str, Any] = {}
        for kind in ("world", "experience", "observation"):
            try:
                out[kind] = int((await self.client.alist_memories(self.bank_id, type=kind, limit=1)).total)
            except Exception:
                out[kind] = None
        self._stats_cache = (now, out)
        return out

    # ---- retain ------------------------------------------------------------
    async def retain_case(self, case: dict[str, Any], root_cause: str, note: str,
                          agent_root_cause: str | None = None, agent_confidence: float | None = None) -> None:
        rc = BY_CODE.get(root_cause)
        content = prompts.resolved_case_content(case, root_cause, rc.label if rc else root_cause, note,
                                                agent_root_cause, agent_confidence)
        metadata = {
            "case_id": case["case_id"],
            "root_cause": root_cause,
            "bank": case["payment"]["creditor_bank"]["code"],
            "reason_code": case.get("reason_code") or case["exception_type"],
            "client": case["payment"]["debtor"]["client_id"],
        }
        if agent_root_cause:
            metadata["agent_was_right"] = str(agent_root_cause == root_cause).lower()
        base = dict(content=content, timestamp=datetime.fromisoformat(case["created_at"]),
                    context="resolved payment exception", document_id=case["case_id"], metadata=metadata,
                    tags=_case_tags(case), retain_async=False)
        extras = dict(entities=_case_entities(case), update_mode="replace") if self._rich_retain else {}
        from hindsight_client_api.exceptions import ApiException

        try:
            await self.client.aretain(self.bank_id, **base, **extras)
        except ApiException as exc:
            # If a server version rejects the optional hints (entity types / update mode), keep going without them.
            if exc.status in (400, 422) and extras:
                log.warning("retain rejected optional fields (%s); retrying without them", exc.status)
                self._rich_retain = False
                await self.client.aretain(self.bank_id, **base)
            else:
                raise
        self._stats_cache = None

    async def forget_case(self, case_id: str) -> None:
        """Delete everything retained for one case (its document). Used to rehearse the live demo."""
        from hindsight_client_api.exceptions import ApiException

        try:
            await self.client.documents.delete_document(bank_id=self.bank_id, document_id=case_id)
        except ApiException as exc:
            if exc.status != 404:
                raise
        self._stats_cache = None

    # ---- recall ------------------------------------------------------------
    async def recall_for_case(self, case: dict[str, Any], limit: int = 8) -> list[MemoryItem]:
        query = prompts.recall_query(case)
        bank_tag = f"bank:{case['payment']['creditor_bank']['code']}"
        general, scoped = await asyncio.gather(
            self.client.arecall(self.bank_id, query=query, types=["world", "experience", "observation"],
                                max_tokens=self.settings.recall_max_tokens, budget=self.settings.recall_budget,
                                query_timestamp=case["created_at"]),
            self.client.arecall(self.bank_id, query=query, types=["world", "experience"], max_tokens=600,
                                budget="low", tags=[bank_tag], tags_match="any_strict",
                                query_timestamp=case["created_at"]),
            return_exceptions=True,
        )
        results: list[Any] = []
        for resp in (general, scoped):
            if isinstance(resp, Exception):
                log.warning("recall arm failed: %s", resp)
                continue
            results.extend(resp.results or [])
        if isinstance(general, Exception) and isinstance(scoped, Exception):
            raise MemoryUnavailable(str(general))

        # Only memories from before this case "happened" may be used (matters for the replay).
        cutoff = case["created_at"]
        items: list[MemoryItem] = []
        seen: set[str] = set()
        for r in results:
            if r.id in seen:
                continue
            seen.add(r.id)
            meta = r.metadata or {}
            # mentioned_at = when the memory was recorded (the resolved case's date)
            occurred = r.mentioned_at or r.occurred_start
            if meta.get("case_id") == case["case_id"]:
                continue  # never let a case see its own resolution
            if occurred and _is_after(occurred, cutoff) and r.type != "observation":
                continue
            items.append(MemoryItem(
                ref="", id=r.id, text=r.text.strip(), type=r.type, occurred=occurred,
                case_id=meta.get("case_id") or r.document_id, root_cause=meta.get("root_cause"),
                tags=list(r.tags or []), score=getattr(r.scores, "final", None) if r.scores else None,
            ))
        # observations first (they are the consolidated lessons), then by score
        items.sort(key=lambda m: (m.type != "observation", -(m.score or 0)))
        items = items[:limit]
        for i, m in enumerate(items, 1):
            m.ref = f"M{i}"
        return items

    # ---- observations / mental model ---------------------------------------
    async def lessons(self, limit: int = 30) -> list[dict[str, Any]]:
        resp = await self.client.alist_memories(self.bank_id, type="observation", limit=100)
        rows = []
        for it in resp.items:
            rows.append({
                "id": it.id, "text": it.text, "proof_count": it.proof_count or 1,
                "updated_at": it.updated_at or it.mentioned_at, "tags": it.tags or [],
                "sources": len(it.source_memory_ids or []),
            })
        rows.sort(key=lambda r: r["updated_at"] or "", reverse=True)  # newest first...
        rows.sort(key=lambda r: r["proof_count"] or 0, reverse=True)  # ...within best-evidenced first
        return rows[:limit]

    async def playbook(self) -> dict[str, Any]:
        from hindsight_client_api.exceptions import ApiException

        try:
            mm = await self.client.aget_mental_model(self.bank_id, prompts.PLAYBOOK_ID, detail="full")
        except ApiException as exc:
            if exc.status == 404:
                await self._ensure_playbook()
                return {"content": "", "status": "building", "is_stale": True}
            raise
        return {
            "content": mm.content or "",
            "last_refreshed_at": mm.last_refreshed_at,
            "is_stale": mm.is_stale,
            "status": "ready" if mm.content else "building",
        }

    async def refresh_playbook(self) -> dict[str, Any]:
        await self.client.arefresh_mental_model(self.bank_id, prompts.PLAYBOOK_ID)
        return {"status": "refreshing"}

    # ---- reflect -----------------------------------------------------------
    async def ask(self, question: str) -> AskResult:
        t0 = time.perf_counter()
        resp = await self.client.areflect(self.bank_id, query=question, budget="mid", include_facts=True,
                                          context="question from a payment-operations analyst")
        based = resp.based_on
        return AskResult(
            answer=resp.text,
            memories=[_dump(m) for m in (based.memories or [])] if based else [],
            mental_models=[_dump(m) for m in (based.mental_models or [])] if based else [],
            directives=[_dump(d) for d in (based.directives or [])] if based else [],
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def precheck(self, req: PrecheckRequest) -> PrecheckResult:
        t0 = time.perf_counter()
        resp = await self.client.areflect(self.bank_id, query=prompts.precheck_query(req), budget="mid",
                                          context="pre-flight check before sending a payment",
                                          response_schema=prompts.PRECHECK_SCHEMA, include_facts=True)
        data = resp.structured_output or {}
        warnings = []
        for w in data.get("warnings") or []:
            if not isinstance(w, dict) or not w.get("title"):
                continue
            warnings.append(PrecheckWarning(title=str(w.get("title")), detail=str(w.get("detail", "")),
                                            severity=_level(w.get("severity")), fix=str(w.get("fix", ""))))
        based = resp.based_on
        return PrecheckResult(
            risk_level=_level(data.get("risk_level"), default="high" if warnings else "low"),
            summary=str(data.get("summary") or resp.text[:600]),
            warnings=warnings,
            evidence=[_dump(m) for m in (based.memories or [])][:8] if based else [],
            directives=[_dump(d) for d in (based.directives or [])] if based else [],
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def close(self) -> None:
        try:
            await self.client.aclose()
        except Exception:
            pass


def _dump(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(obj)


def _level(value: Any, default: str = "medium") -> str:
    v = str(value or "").strip().lower()
    return v if v in ("low", "medium", "high") else default


def _is_after(occurred: str, cutoff_iso: str) -> bool:
    try:
        a = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        b = datetime.fromisoformat(cutoff_iso)
        if a.tzinfo is None:
            a = a.replace(tzinfo=b.tzinfo)
        return a > b
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Offline stand-in (no network). Clearly not the product's learning engine.
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]{3,}")


class LocalMemory:
    backend = "local"

    def __init__(self, settings: Settings, path: Path | None = None):
        self.settings = settings
        self.bank_id = settings.bank_id + "-offline"
        self.path = path or (settings.data_dir / "local_memory.json")
        self.items: list[dict[str, Any]] = []
        self.ready = True
        self.last_error = None
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text())
            except Exception:
                self.items = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, indent=1))

    async def setup(self) -> dict[str, Any]:
        return {"bank_id": self.bank_id, "directives": [d["name"] for d in prompts.DIRECTIVES], "playbook": "offline"}

    async def reset(self) -> None:
        self.items = []
        self._save()

    async def count(self) -> int:
        return len(self.items)

    async def stats(self) -> dict[str, Any]:
        return {"world": len(self.items), "experience": 0, "observation": len(self._groups())}

    async def retain_case(self, case, root_cause, note, agent_root_cause=None, agent_confidence=None) -> None:
        rc = BY_CODE.get(root_cause)
        text = prompts.resolved_case_content(case, root_cause, rc.label if rc else root_cause, note,
                                             agent_root_cause, agent_confidence)
        self.items = [i for i in self.items if i["case_id"] != case["case_id"]]
        self.items.append({
            "id": f"local-{case['case_id']}", "case_id": case["case_id"], "text": text,
            "occurred": case["created_at"], "root_cause": root_cause,
            "bank": case["payment"]["creditor_bank"]["code"], "bank_name": case["payment"]["creditor_bank"]["name"],
            "code": case.get("reason_code") or case["exception_type"], "tags": _case_tags(case),
        })
        self._save()

    async def forget_case(self, case_id: str) -> None:
        self.items = [i for i in self.items if i["case_id"] != case_id]
        self._save()

    async def recall_for_case(self, case: dict[str, Any], limit: int = 8) -> list[MemoryItem]:
        q = set(_WORD.findall(prompts.recall_query(case).lower()))
        bank = case["payment"]["creditor_bank"]["code"]
        code = case.get("reason_code") or case["exception_type"]
        scored = []
        for it in self.items:
            if it["case_id"] == case["case_id"] or it["occurred"] > case["created_at"]:
                continue
            words = set(_WORD.findall(it["text"].lower()))
            score = len(q & words) / (len(q) or 1)
            score += 0.5 if it["bank"] == bank else 0
            score += 0.3 if it["code"] == code else 0
            scored.append((score, it))
        scored.sort(key=lambda x: -x[0])
        out = []
        for i, (score, it) in enumerate(scored[:limit], 1):
            out.append(MemoryItem(ref=f"M{i}", id=it["id"], text=it["text"], type="world", occurred=it["occurred"],
                                  case_id=it["case_id"], root_cause=it["root_cause"], tags=it["tags"],
                                  score=round(score, 3)))
        return out

    def _groups(self) -> dict[tuple[str, str], list[dict]]:
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for it in self.items:
            groups[(it["bank_name"], it["root_cause"])].append(it)
        return {k: v for k, v in groups.items() if len(v) >= 2}

    async def lessons(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = []
        for (bank_name, rc), its in self._groups().items():
            label = BY_CODE[rc].label if rc in BY_CODE else rc
            last = max(i["occurred"] for i in its)[:10]
            rows.append({"id": f"{bank_name}-{rc}", "text": f"[offline] {bank_name}: {label} ({len(its)} cases, last {last}).",
                         "proof_count": len(its), "updated_at": last, "tags": [], "sources": len(its)})
        rows.sort(key=lambda r: -r["proof_count"])
        return rows[:limit]

    async def playbook(self) -> dict[str, Any]:
        lines = ["# Payment Exception Playbook (offline preview)", "",
                 "_Offline mode groups past cases by bank and root cause. Connect Hindsight for the real, self-updating playbook._", ""]
        for row in await self.lessons():
            lines.append(f"- {row['text'].replace('[offline] ', '')}")
        return {"content": "\n".join(lines), "status": "ready", "is_stale": False, "last_refreshed_at": None}

    async def refresh_playbook(self) -> dict[str, Any]:
        return {"status": "ready"}

    async def ask(self, question: str) -> AskResult:
        fake_case = {"case_id": "-", "created_at": "9999", "exception_type": "QUESTION", "reason_code": None,
                     "reason_text": None, "counterparty_message": question,
                     "payment": {"creditor_bank": {"name": "", "bic": "", "code": ""}, "debtor": {"name": "", "client_id": ""},
                                 "creditor": {"name": ""}, "currency": "", "amount": 0,
                                 "submitted_at": "2026-01-01T00:00:00+08:00"}}
        hits = await self.recall_for_case(fake_case, limit=4)
        body = "\n".join(f"- {h.text.splitlines()[0]} ... {h.text.splitlines()[-2] if len(h.text.splitlines()) > 2 else ''}" for h in hits)
        return AskResult(answer=f"**Offline mode** - closest past cases:\n\n{body or '- none yet'}",
                         memories=[{"id": h.id, "text": h.text[:300]} for h in hits])

    async def precheck(self, req: PrecheckRequest) -> PrecheckResult:
        warnings = []
        for row in await self.lessons():
            if req.beneficiary_bank.lower() in row["text"].lower():
                warnings.append(PrecheckWarning(title="Seen before at this bank", detail=row["text"], severity="medium",
                                                fix="Check the playbook entry before sending."))
        return PrecheckResult(risk_level="medium" if warnings else "low",
                              summary="Offline heuristic check (connect Hindsight for the real pre-flight check).",
                              warnings=warnings)

    async def close(self) -> None:
        return None


def build_memory(settings: Settings, backend: str):
    return HindsightMemory(settings) if backend == "hindsight" else LocalMemory(settings)
