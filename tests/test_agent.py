import asyncio
import copy

from app.agent import DejaVuAgent, HeuristicReasoner
from app.config import Settings
from app.llm import LLMError
from app.memory import LocalMemory
from app.models import ResolveRequest
from app.store import Store
from app.taxonomy import UNKNOWN


def _agent(tmp_path, reasoner=None, memory=None, with_store=False):
    s = Settings()
    s.data_dir = tmp_path
    mem = memory or LocalMemory(s, path=tmp_path / "mem.json")
    store = Store(tmp_path / "t.db") if with_store else None
    return DejaVuAgent(s, mem, reasoner or HeuristicReasoner(), store), mem, store


def _first(cases, bank, code=None):
    return next(c for c in cases if c["payment"]["creditor_bank"]["code"] == bank and (code is None or c.get("reason_code") == code))


def test_agent_learns_a_bank_specific_pattern(tmp_path, history, live):
    agent, mem, _ = _agent(tmp_path)
    new_case = _first(live, "PDCB", "AC01")
    before = asyncio.run(agent.diagnose(new_case, use_memory=True))
    assert before.root_cause == "CLIENT_TYPO_IN_DETAILS"  # nothing learned yet

    teach = _first([c for c in history if c["ground_truth"]["root_cause"] == "ACCOUNT_FORMAT_CHANGED"], "PDCB")
    asyncio.run(mem.retain_case(teach, "ACCOUNT_FORMAT_CHANGED", teach["ground_truth"]["resolution_note"]))

    after = asyncio.run(agent.diagnose(new_case, use_memory=True))
    assert after.root_cause == "ACCOUNT_FORMAT_CHANGED"
    assert after.evidence and after.evidence[0].case_id == teach["case_id"]
    off = asyncio.run(agent.diagnose(new_case, use_memory=False))
    assert off.root_cause == "CLIENT_TYPO_IN_DETAILS" and not off.recalled


def test_a_case_never_sees_its_own_resolution_or_the_future(tmp_path, history):
    agent, mem, _ = _agent(tmp_path)
    case = history[10]
    later = history[20]
    asyncio.run(mem.retain_case(case, case["ground_truth"]["root_cause"], "note"))
    asyncio.run(mem.retain_case(later, later["ground_truth"]["root_cause"], "note"))
    recalled = asyncio.run(mem.recall_for_case(case))
    assert all(m.case_id not in (case["case_id"], later["case_id"]) for m in recalled)


class _BrokenReasoner:
    async def diagnose(self, case, memories):
        raise LLMError("model down")


class _HallucinatingReasoner:
    async def diagnose(self, case, memories):
        return {"root_cause": "client typo in details", "confidence": 0.99, "reasoning": "r",
                "evidence": [{"memory_ref": "M42", "why_relevant": "made up"}], "recommended_action": "a",
                "draft_message": "send to 601620144880913", "prevention_tip": ""}, "fake"


def test_model_failure_degrades_to_manual_review(tmp_path, live):
    agent, _, _ = _agent(tmp_path, reasoner=_BrokenReasoner())
    d = asyncio.run(agent.diagnose(live[0]))
    assert d.root_cause == UNKNOWN and d.degraded and d.requires_human_approval and d.warnings


def test_hallucinated_evidence_is_dropped_and_confidence_capped(tmp_path, live):
    agent, _, _ = _agent(tmp_path, reasoner=_HallucinatingReasoner())
    case = _first(live, "RHHB")
    d = asyncio.run(agent.diagnose(case))
    assert d.root_cause == "CLIENT_TYPO_IN_DETAILS"
    assert d.evidence == []
    assert d.confidence <= 0.6
    assert "601620144880913" not in d.draft_message


class _DownMemory(LocalMemory):
    fail = True

    async def recall_for_case(self, case, limit=8):
        raise RuntimeError("network")

    async def retain_case(self, *a, **k):
        if self.fail:
            raise RuntimeError("network")
        return await super().retain_case(*a, **k)


def test_memory_outage_never_blocks_the_desk(tmp_path, history, live):
    s = Settings()
    mem = _DownMemory(s, path=tmp_path / "m.json")
    agent, _, store = _agent(tmp_path, memory=mem, with_store=True)
    store.load_if_empty(history, live)
    case = store.get_case(live[1]["case_id"])
    d = asyncio.run(agent.diagnose(case))
    assert d.degraded and d.warnings

    res = asyncio.run(agent.resolve(case, ResolveRequest(root_cause="ACCOUNT_FORMAT_CHANGED", note="prefixed 601")))
    assert not res.retained and res.queued_for_retry
    assert store.counts()["pending_retains"] == 1

    mem.fail = False
    assert asyncio.run(agent.flush_outbox()) == 1
    assert store.counts()["pending_retains"] == 0
    assert asyncio.run(mem.count()) == 1


def test_resolve_records_whether_the_agent_was_right(tmp_path, live):
    agent, mem, _ = _agent(tmp_path)
    case = copy.deepcopy(live[0])
    res = asyncio.run(agent.resolve(case, ResolveRequest(root_cause="CLIENT_ERP_DUPLICATE", note="erp resend",
                                                         agent_root_cause="POSSIBLE_DUPLICATE_VERIFY", agent_confidence=0.5)))
    assert res.agent_was_right is False and res.retained
    stored = mem.items[0]["text"]
    assert "which was wrong" in stored and "CLIENT_ERP_DUPLICATE" in stored
